# -*- coding: utf-8 -*-
"""示例插件「材料刷新计时」。

演示：注册插件视图、私有 storage 持久化、插件级定时器、结束提示。
计时全部按系统时钟计算：关闭程序再打开，剩余时间依然准确。
"""
from __future__ import annotations

import time
import uuid

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QFont, QPainter
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from endbox.plugins.api import EndBoxPlugin
from endbox.core.text import t
from endbox.theme import fonts as F
from endbox.theme import paint
from endbox.ui.kit import (
    CutButton,
    EmptyState,
    FieldText,
    IconButton,
    Label,
    Panel,
    ScrollArea,
    Segmented,
    confirm,
)
from endbox.ui.kit.base import EWidget, pal
from endbox.ui.views.base import BaseView


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


class _TimerRow(EWidget):
    """一行倒计时：名称 / 总长 / 剩余时间 / 进度条 + 红色删除按钮。"""

    HEIGHT = 58

    def __init__(self, plugin: "Plugin", timer: dict, on_delete, parent=None):
        super().__init__(parent)
        self._plugin = plugin
        self._timer = timer
        self._remaining = plugin.remaining(timer)
        self._finished = self._remaining <= 0 or bool(timer.get("finished_at"))
        self.setFixedHeight(self.HEIGHT)
        self._delete = IconButton("trash", t("plugins.timers.delete_tooltip"), variant="danger")
        self._delete.clicked.connect(lambda: on_delete(timer.get("id")))
        self._delete.setParent(self)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._delete.move(
            self.width() - self._delete.width() - 2,
            (self.height() - self._delete.height()) // 2,
        )

    def update_remaining(self) -> None:
        remaining = self._plugin.remaining(self._timer)
        finished = remaining <= 0 or bool(self._timer.get("finished_at"))
        if finished != self._finished or abs(remaining - self._remaining) >= 0.2:
            self._remaining = remaining
            self._finished = finished
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        content_w = max(80, self.width() - self._delete.width() - 14)
        right_w = 112.0
        name = str(self._timer.get("name") or t("plugins.timers.default_name"))
        paint.draw_text(
            p,
            QRectF(0, 6, max(40, content_w - right_w - 8), 20),
            name,
            F.font(15, weight=QFont.Weight.Bold, cjk_first=True),
            colors.ink,
            elide=True,
        )
        started = time.strftime("%H:%M", time.localtime(float(self._timer.get("started_at", 0))))
        total = self._plugin.format_seconds(self._timer.get("seconds", 0))
        paint.draw_text(
            p,
            QRectF(0, 27, max(40, content_w - right_w - 8), 16),
            t("plugins.timers.duration_line", total=total, started=started),
            F.font(11, cjk_first=True),
            colors.ink2,
            elide=True,
        )
        if self._finished:
            paint.draw_text(
                p,
                QRectF(content_w - right_w, 0, right_w, 30),
                t("plugins.timers.finished"),
                F.font(14, weight=QFont.Weight.Bold, cjk_first=True),
                colors.ink2,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )
        else:
            paint.draw_text(
                p,
                QRectF(content_w - right_w, 0, right_w, 30),
                self._plugin.format_seconds(self._remaining + 0.999),
                F.number_font(22),
                colors.ink,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )
        seconds = max(1.0, float(self._timer.get("seconds", 1)))
        ratio = 1.0 - min(1.0, self._remaining / seconds)
        bar = QRectF(0, self.height() - 7, content_w, 3)
        p.fillRect(bar, colors.chip)
        p.fillRect(QRectF(bar.x(), bar.y(), bar.width() * ratio, bar.height()), colors.signal)
        p.end()


class TimersView(BaseView):
    key = "timers"
    eyebrow = "TIMERS"
    title = t("plugins.timers.title")
    icon = "clock"
    order = 10
    sidebar_label = t("plugins.timers.sidebar_label")
    description = t("plugins.timers.description")

    #: 由插件在 on_load() 里注入（视图实例由宿主用 AppContext 构造，不经过插件）
    PLUGIN: "Plugin | None" = None

    def build(self) -> None:
        self._rows: list[_TimerRow] = []

        scroll = ScrollArea(self)
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        new_panel = Panel(t("plugins.timers.new_panel"), "NEW TIMER", padding=16, spacing=10)
        name_row = QWidget()
        name_layout = QHBoxLayout(name_row)
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.setSpacing(8)
        name_layout.addWidget(Label(t("plugins.timers.name_label"), "body"))
        self._name = QLineEdit()
        self._name.setPlaceholderText(t("plugins.timers.placeholder"))
        self._name.setFixedHeight(32)
        name_layout.addWidget(self._name, 1)
        new_panel.add(name_row)

        time_row = QWidget()
        time_layout = QHBoxLayout(time_row)
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.setSpacing(8)
        time_layout.addWidget(Label(t("plugins.timers.duration_label"), "body"))
        self._preset = Segmented(
            [
                t("plugins.timers.preset.5"),
                t("plugins.timers.preset.10"),
                t("plugins.timers.preset.30"),
                t("plugins.timers.preset.60"),
            ],
            index=1,
        )
        time_layout.addWidget(self._preset)
        add_btn = CutButton(t("plugins.timers.add"), "primary", icon="plus")
        add_btn.clicked.connect(self._add)
        time_layout.addWidget(add_btn)
        time_layout.addStretch(1)
        new_panel.add(time_row)
        new_panel.add(
            Label(
                t("plugins.timers.help"),
                "muted",
                wrap=True,
            )
        )
        layout.addWidget(new_panel)

        self._list_panel = Panel(t("plugins.timers.list_panel"), "TIMERS", padding=16, spacing=10)
        clear_btn = CutButton(t("plugins.timers.clear_finished"), "ghost", icon="trash", compact=True)
        clear_btn.clicked.connect(self._clear_finished)
        self._list_panel.add_header_action(clear_btn)
        self._rows_host = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(6)
        self._list_panel.add(self._rows_host)
        self._empty = EmptyState(
            t("plugins.timers.empty.title"),
            t("plugins.timers.empty.desc"),
        )
        # EmptyState 的高度按「有操作按钮」计算，无按钮时描述行会被裁掉，这里固定一个够高的值
        self._empty.setFixedHeight(126)
        self._list_panel.add(self._empty)
        layout.addWidget(self._list_panel)

        self._stats = FieldText(["—"])
        layout.addWidget(self._stats)
        layout.addStretch(1)

        scroll.set_content(host)
        self._root.addWidget(scroll)

        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._tick)
        self._name.returnPressed.connect(self._add)
        self.refresh()

    # -- 生命周期 -----------------------------------------------------------
    def on_show(self) -> None:
        self.refresh()
        self._timer.start()

    def on_hide(self) -> None:
        self._timer.stop()

    # -- 数据 ---------------------------------------------------------------
    def refresh(self) -> None:
        if not hasattr(self, "_rows_layout"):
            return
        plugin = self.PLUGIN
        if plugin is None:
            return
        _clear_layout(self._rows_layout)
        self._rows = []
        timers = plugin.timers()
        for timer in timers:
            row = _TimerRow(plugin, timer, self._delete)
            self._rows_layout.addWidget(row)
            self._rows.append(row)
        self._rows_host.setVisible(bool(timers))
        self._empty.setVisible(not timers)
        self._update_stats(timers)

    def _tick(self) -> None:
        plugin = self.PLUGIN
        if plugin is None:
            return
        for row in self._rows:
            row.update_remaining()
        self._update_stats(plugin.timers())

    def _update_stats(self, timers: list[dict]) -> None:
        finished = sum(1 for t in timers if t.get("finished_at"))
        self._stats.set_fields(
            [
                t("plugins.timers.stats.active", count=len(timers) - finished),
                t("plugins.timers.stats.finished", count=finished),
                t("plugins.timers.stats.total", count=len(timers)),
            ]
        )

    # -- 操作 ---------------------------------------------------------------
    def _add(self) -> None:
        plugin = self.PLUGIN
        if plugin is None:
            return
        name = self._name.text().strip() or t(
            "plugins.timers.auto_name", index=len(plugin.timers()) + 1
        )
        seconds = Plugin.PRESETS[self._preset.index][1]
        plugin.add_timer(name, seconds)
        self._name.clear()
        self.refresh()
        self.ctx.notify(t("plugins.timers.started_toast", name=name, duration=plugin.format_seconds(seconds)), "ok")

    def _delete(self, timer_id: str) -> None:
        plugin = self.PLUGIN
        if plugin is None:
            return
        timer = next((item for item in plugin.timers() if item.get("id") == timer_id), None)
        name = str((timer or {}).get("name") or t("plugins.timers.default_name"))
        if not confirm(
            self,
            title=t("plugins.timers.confirm.delete_title"),
            message=t("plugins.timers.confirm.delete_message", name=name),
            action_text=t("plugins.timers.confirm.delete_action"),
        ):
            return
        plugin.remove_timer(timer_id)
        self.refresh()

    def _clear_finished(self) -> None:
        plugin = self.PLUGIN
        if plugin is None:
            return
        finished = sum(1 for item in plugin.timers() if item.get("finished_at"))
        if not finished:
            self.ctx.notify(t("plugins.timers.cleared_toast", count=0), "info")
            return
        if not confirm(
            self,
            title=t("plugins.timers.confirm.clear_title"),
            message=t("plugins.timers.confirm.clear_message"),
            action_text=t("plugins.timers.confirm.clear_action"),
        ):
            return
        removed = plugin.clear_finished()
        self.refresh()
        self.ctx.notify(t("plugins.timers.cleared_toast", count=removed), "info")


class Plugin(EndBoxPlugin):
    """计时插件本体：持有数据与全局「到点检查」定时器。"""

    PRESETS = [
        (t("plugins.timers.preset.5"), 300),
        (t("plugins.timers.preset.10"), 600),
        (t("plugins.timers.preset.30"), 1800),
        (t("plugins.timers.preset.60"), 3600),
    ]
    STORAGE_KEY = "timers"

    def on_load(self) -> None:
        TimersView.PLUGIN = self
        self._timers: list[dict] = self._coerce_timers(self.ctx.storage.get(self.STORAGE_KEY, []))
        self._notified: set[str] = set()
        self._ticker = QTimer()
        self._ticker.setInterval(1000)
        self._ticker.timeout.connect(self._tick)
        self._ticker.start()
        self._restore_elapsed()
        self.ctx.register_view(
            TimersView,
            sidebar_icon="clock",
            sidebar_label=t("plugins.timers.sidebar_label"),
            order=10,
        )
        self.ctx.logger.info("timers ready: %d 个计时", len(self._timers))

    def on_enable(self) -> None:
        self.ctx.logger.info("timers enabled")

    def on_disable(self) -> None:
        self._stop_ticker()

    def on_unload(self) -> None:
        self._stop_ticker()
        self._save()
        TimersView.PLUGIN = None

    def _stop_ticker(self) -> None:
        ticker = getattr(self, "_ticker", None)
        if ticker is not None:
            ticker.stop()
            self._ticker = None

    # -- 数据 ---------------------------------------------------------------
    @staticmethod
    def format_seconds(value: float) -> str:
        total = max(0, int(round(float(value))))
        hours, rest = divmod(total, 3600)
        minutes, seconds = divmod(rest, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _coerce_timers(raw) -> list[dict]:
        timers: list[dict] = []
        if not isinstance(raw, list):
            return timers
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                continue
            try:
                seconds = float(item.get("seconds", 0))
                started_at = float(item.get("started_at", 0))
            except (TypeError, ValueError):
                continue
            if seconds <= 0 or started_at <= 0:
                continue
            timer = {
                "id": str(item.get("id") or f"timer_{index}"),
                "name": str(item.get("name") or t("plugins.timers.default_name"))[:40],
                "seconds": int(max(1, min(86400, seconds))),
                "started_at": started_at,
            }
            if item.get("finished_at"):
                try:
                    timer["finished_at"] = float(item["finished_at"])
                except (TypeError, ValueError):
                    timer["finished_at"] = started_at + seconds
            timers.append(timer)
        return timers

    def timers(self) -> list[dict]:
        return [dict(timer) for timer in self._timers]

    def remaining(self, timer: dict) -> float:
        if timer.get("finished_at"):
            return 0.0
        try:
            deadline = float(timer.get("started_at", 0)) + float(timer.get("seconds", 0))
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, deadline - time.time())

    def add_timer(self, name: str, seconds: int) -> dict:
        timer = {
            "id": uuid.uuid4().hex[:8],
            "name": str(name or t("plugins.timers.default_name"))[:40],
            "seconds": int(max(1, min(86400, seconds))),
            "started_at": time.time(),
        }
        self._timers.append(timer)
        self._save()
        self.ctx.logger.info("timer added: %s %ss", timer["name"], timer["seconds"])
        return timer

    def remove_timer(self, timer_id: str) -> None:
        self._timers = [timer for timer in self._timers if timer.get("id") != timer_id]
        self._save()

    def clear_finished(self) -> int:
        before = len(self._timers)
        self._timers = [timer for timer in self._timers if not timer.get("finished_at")]
        removed = before - len(self._timers)
        if removed:
            self._save()
        return removed

    def _restore_elapsed(self) -> None:
        """启动时已经过期的计时直接标记结束，不补发一堆提示。"""
        now = time.time()
        changed = False
        for timer in self._timers:
            if timer.get("finished_at"):
                self._notified.add(str(timer.get("id")))
                continue
            if now >= float(timer.get("started_at", 0)) + float(timer.get("seconds", 0)):
                timer["finished_at"] = now
                self._notified.add(str(timer.get("id")))
                changed = True
        if changed:
            self._save()

    def _tick(self) -> None:
        now = time.time()
        changed = False
        for timer in self._timers:
            if timer.get("finished_at"):
                continue
            deadline = float(timer.get("started_at", 0)) + float(timer.get("seconds", 0))
            if now >= deadline:
                timer["finished_at"] = now
                changed = True
                timer_id = str(timer.get("id"))
                if timer_id not in self._notified:
                    self._notified.add(timer_id)
                    self.ctx.notify(
                    t("plugins.timers.finished_toast", name=timer.get("name") or t("plugins.timers.default_name")),
                    "warn",
                )
        if changed:
            self._save()

    def _save(self) -> None:
        self.ctx.storage.set(self.STORAGE_KEY, self._timers)
