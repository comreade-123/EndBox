# -*- coding: utf-8 -*-
"""示例插件「连携备忘录」（市场安装示例）。

记录干员 / 组合的连携触发条件，保存在插件私有 storage 里。
删除单条记录必须经过确认框；不需要任何权限。
"""
from __future__ import annotations

import time
import uuid

from PyQt6.QtWidgets import QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from endbox.core.text import t
from endbox.plugins.api import EndBoxPlugin
from endbox.ui.kit import (
    CutButton,
    EmptyState,
    FieldText,
    Hairline,
    IconButton,
    Label,
    Panel,
    ScrollArea,
    confirm,
)
from endbox.ui.views.base import BaseView


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


class LinkNotesView(BaseView):
    key = "link_notes"
    eyebrow = "LINK NOTES"
    title = t("plugins.link_notes.title")
    icon = "link"
    order = 40
    sidebar_label = t("plugins.link_notes.sidebar_label")
    description = t("plugins.link_notes.description")

    PLUGIN: "Plugin | None" = None

    def build(self) -> None:
        scroll = ScrollArea(self)
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        new_panel = Panel(
            t("plugins.link_notes.panel.new"), "NEW NOTE", padding=16, spacing=10
        )
        name_row = QWidget()
        name_layout = QHBoxLayout(name_row)
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.setSpacing(8)
        name_layout.addWidget(Label(t("plugins.link_notes.name_label"), "body"))
        self._name = QLineEdit()
        self._name.setPlaceholderText(t("plugins.link_notes.name_placeholder"))
        self._name.setFixedHeight(32)
        name_layout.addWidget(self._name, 1)
        new_panel.add(name_row)

        condition_row = QWidget()
        condition_layout = QHBoxLayout(condition_row)
        condition_layout.setContentsMargins(0, 0, 0, 0)
        condition_layout.setSpacing(8)
        condition_layout.addWidget(Label(t("plugins.link_notes.condition_label"), "body"))
        self._condition = QLineEdit()
        self._condition.setPlaceholderText(t("plugins.link_notes.condition_placeholder"))
        self._condition.setFixedHeight(32)
        condition_layout.addWidget(self._condition, 1)
        add_btn = CutButton(t("plugins.link_notes.add"), "primary", icon="plus")
        add_btn.clicked.connect(self._add)
        condition_layout.addWidget(add_btn)
        new_panel.add(condition_row)
        new_panel.add(Label(t("plugins.link_notes.help"), "muted", wrap=True))
        layout.addWidget(new_panel)

        self._list_panel = Panel(
            t("plugins.link_notes.panel.list"), "NOTES", padding=16, spacing=8
        )
        self._stats = FieldText([t("plugins.link_notes.stats", count=0)])
        self._list_panel.add_header_action(self._stats)
        self._rows_host = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(0)
        self._list_panel.add(self._rows_host)
        self._empty = EmptyState(
            t("plugins.link_notes.empty.title"),
            t("plugins.link_notes.empty.desc"),
        )
        self._empty.setFixedHeight(126)
        self._list_panel.add(self._empty)
        layout.addWidget(self._list_panel)
        layout.addStretch(1)
        scroll.set_content(host)
        self._root.addWidget(scroll)

        self._name.returnPressed.connect(self._add)
        self._condition.returnPressed.connect(self._add)
        self.refresh()

    # -- 数据 ---------------------------------------------------------------
    def refresh(self) -> None:
        if not hasattr(self, "_rows_layout"):
            return
        _clear_layout(self._rows_layout)
        plugin = self.PLUGIN
        notes = plugin.notes() if plugin is not None else []
        self._stats.set_fields([t("plugins.link_notes.stats", count=len(notes))])
        if not notes:
            self._rows_host.hide()
            self._empty.show()
            return
        self._rows_host.show()
        self._empty.hide()
        for note in notes:
            self._rows_layout.addWidget(self._note_row(note))
            self._rows_layout.addWidget(Hairline())

    def _note_row(self, note: dict) -> QWidget:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 7, 0, 7)
        row_layout.setSpacing(10)
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        text_box.addWidget(Label(str(note.get("name") or "—"), "body"))
        text_box.addWidget(Label(str(note.get("condition") or "—"), "muted", wrap=True))
        text_box.addWidget(
            Label(
                t(
                    "plugins.link_notes.row.meta",
                    time=time.strftime("%m-%d %H:%M", time.localtime(float(note.get("created_at") or 0))),
                ),
                "small",
            )
        )
        row_layout.addLayout(text_box, 1)
        delete_btn = IconButton(
            "trash", t("plugins.link_notes.delete_tooltip"), variant="danger"
        )
        delete_btn.clicked.connect(lambda: self._delete(note))
        row_layout.addWidget(delete_btn, 0)
        return row

    # -- 操作 ---------------------------------------------------------------
    def _add(self) -> None:
        plugin = self.PLUGIN
        if plugin is None:
            return
        name = self._name.text().strip()
        condition = self._condition.text().strip()
        if not name:
            self.ctx.notify(t("plugins.link_notes.error.name_required"), "warn")
            return
        if not condition:
            self.ctx.notify(t("plugins.link_notes.error.condition_required"), "warn")
            return
        plugin.add_note(name, condition)
        self._name.clear()
        self._condition.clear()
        self.refresh()
        self.ctx.notify(t("plugins.link_notes.added", name=name), "ok")

    def _delete(self, note: dict) -> None:
        plugin = self.PLUGIN
        if plugin is None:
            return
        if not confirm(
            self,
            title=t("plugins.link_notes.confirm.delete_title"),
            message=t(
                "plugins.link_notes.confirm.delete_message",
                name=str(note.get("name") or "—"),
            ),
            action_text=t("plugins.link_notes.confirm.delete_action"),
        ):
            return
        plugin.remove_note(str(note.get("id") or ""))
        self.refresh()


class Plugin(EndBoxPlugin):
    STORAGE_KEY = "notes"

    def on_load(self) -> None:
        LinkNotesView.PLUGIN = self
        self._notes = self._coerce(self.ctx.storage.get(self.STORAGE_KEY, []))
        self.ctx.register_view(
            LinkNotesView,
            sidebar_icon="link",
            sidebar_label=t("plugins.link_notes.sidebar_label"),
            order=40,
        )
        self.ctx.logger.info("link_notes ready: %d 条", len(self._notes))

    def on_unload(self) -> None:
        LinkNotesView.PLUGIN = None

    @staticmethod
    def _coerce(raw) -> list[dict]:
        notes: list[dict] = []
        if not isinstance(raw, list):
            return notes
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            condition = str(item.get("condition") or "").strip()
            if not name:
                continue
            notes.append(
                {
                    "id": str(item.get("id") or f"note_{index}"),
                    "name": name[:60],
                    "condition": condition[:300],
                    "created_at": float(item.get("created_at") or 0),
                }
            )
        return notes

    def notes(self) -> list[dict]:
        return [dict(note) for note in self._notes]

    def add_note(self, name: str, condition: str) -> dict:
        note = {
            "id": uuid.uuid4().hex[:8],
            "name": str(name or "")[:60],
            "condition": str(condition or "")[:300],
            "created_at": time.time(),
        }
        self._notes.append(note)
        self._save()
        return note

    def remove_note(self, note_id: str) -> None:
        self._notes = [note for note in self._notes if note.get("id") != note_id]
        self._save()

    def _save(self) -> None:
        self.ctx.storage.set(self.STORAGE_KEY, self._notes)
