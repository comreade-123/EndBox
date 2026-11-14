# -*- coding: utf-8 -*-
"""技能轴视图（模块二，v3）：按键提示式**有序步骤序列**编辑器 + 按键提示运行态。

左侧顶部是 4 人编队；中间是步骤序列（战技/连携/终结/切换干员/重击/注释/换行/分段/反复），
没有任何时间刻度/播放头/时长；右下统计真实步骤/分段/反复数量与预计总耗时/技力消耗。

运行态：开始运行后弹出置顶鼠标穿透提示条，按提示键才前进；F8 退出 / F9 回第一段 /
F6-F7 透明度 / F10 反复开关 / F11 回反复起点。非管理员会提示 UIPI 并可一键提权重启。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from endbox.core.event_bus import Topics
from endbox.core.text import t as tr
from endbox.modules.timeline.model import BLOCK_KINDS, KIND_LABELS, SLOT_COUNT
from endbox.modules.timeline.ui_canvas import PixmapCache, SequenceCanvas
from endbox.modules.timeline.ui_parts import (
    ActionPanel,
    AxisLibraryDialog,
    KeyConfigDialog,
    OperatorDetail,
    OperatorPool,
    RunSettingsDialog,
    ShareDialog,
    StepTimingDialog,
    TeamLibraryDialog,
    TeamSlots,
    TextPromptDialog,
)
from endbox.ui.kit import (
    CutButton,
    confirm,
    EmptyState,
    FieldText,
    Hairline,
    IconButton,
    InfoFlag,
    Label,
    NumberStat,
    ScrollArea,
    SectionHead,
    Segmented,
)
from endbox.ui.kit.base import pal
from endbox.ui.views.base import BaseView

MARKER_KINDS = (
    ("comment", tr("timeline.kind.comment"), "info"),
    ("break", tr("timeline.kind.break"), "list"),
    ("segment", tr("timeline.kind.segment"), "grid"),
    ("loop_start", tr("timeline.kind.loop_start"), "refresh"),
    ("loop_end", tr("timeline.kind.loop_end"), "refresh"),
)


class TimelineView(BaseView):
    key = "timeline"
    eyebrow = "TIMELINE"
    title = tr("timeline.view.title")
    icon = "timeline"
    order = 20
    sidebar_label = tr("timeline.view.sidebar_label")
    description = tr("timeline.view.description")

    def build(self) -> None:
        self._cache = PixmapCache()
        self._run_sub = None
        self._state_sub = None
        self._run_state_cache: dict = {}
        self._minimized_for_run = False
        self._target_slot = 0
        self._canvas: SequenceCanvas | None = None
        self._pool: OperatorPool | None = None
        service = self._service()
        if service is None:
            self._build_missing()
            return
        self._build_full(service)

    # -- 服务缺失 -----------------------------------------------------------
    def _build_missing(self) -> None:
        host = QWidget(self)
        host.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 8, 0, 0)
        action = CutButton(tr("timeline.view.service_missing.action"), "ghost", icon="settings")
        action.clicked.connect(lambda: self.ctx.open_view("settings"))
        layout.addWidget(EmptyState(tr("timeline.view.service_missing.title"),
                                    tr("timeline.view.service_missing.message"), action=action))
        layout.addStretch(1)
        self._root.addWidget(host)

    # -- 搭建 ---------------------------------------------------------------
    def _build_full(self, service) -> None:
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # 编队标题
        team_head = QWidget(host)
        team_head.setFixedHeight(38)
        row = QHBoxLayout(team_head)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        head = SectionHead("TEAM", tr("timeline.view.team.title"), team_head, size=16)
        head.setMinimumWidth(140)
        row.addWidget(head, 0)
        self._team_fields = FieldText(
            [tr("timeline.view.team.slots"), tr("timeline.view.team.pool_count", count=service.assets.count)],
            team_head,
        )
        row.addWidget(self._team_fields, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        self._pool_btn = CutButton(tr("timeline.view.pool_button"), "ghost", team_head, icon="grid", height=28, compact=True)
        self._pool_btn.clicked.connect(lambda: self._open_pool(self._target_slot))
        row.addWidget(self._pool_btn)
        self._quick_team_btn = CutButton(tr("timeline.view.quick_team"), "ghost", team_head, icon="plus", height=28, compact=True)
        self._quick_team_btn.setToolTip(tr("timeline.view.quick_team_tip"))
        self._quick_team_btn.clicked.connect(self._quick_team)
        row.addWidget(self._quick_team_btn)
        self._team_lib_btn = CutButton(tr("timeline.view.team_library"), "ghost", team_head, icon="list", height=28, compact=True)
        self._team_lib_btn.clicked.connect(self._open_team_library)
        row.addWidget(self._team_lib_btn)
        clear_team = CutButton(tr("timeline.view.clear_team"), "ghost", team_head, height=28, compact=True)
        clear_team.clicked.connect(self._clear_team)
        row.addWidget(clear_team)
        layout.addWidget(team_head)

        self._team_slots = TeamSlots(host)
        layout.addWidget(self._team_slots)

        self._warn = InfoFlag("", host, kind="warn")
        self._warn.hide()
        layout.addWidget(self._warn)

        self._detail = OperatorDetail(self._cache, host)
        layout.addWidget(self._detail)

        # 动作面板：按技能类型分行，点干员直接插入（按键提示式）
        self._actions = ActionPanel(host)
        self._actions.insert_requested.connect(self._insert_skill)
        self._actions.empty_slot_clicked.connect(self._on_empty_slot)
        layout.addWidget(self._actions)

        # 标记按钮 + 轴名
        marker_bar = QWidget(host)
        marker_bar.setFixedHeight(28)
        marker_row = QHBoxLayout(marker_bar)
        marker_row.setContentsMargins(0, 0, 0, 0)
        marker_row.setSpacing(6)
        marker_row.addWidget(Label(tr("timeline.actions.eyebrow"), "eyebrow"), 0, Qt.AlignmentFlag.AlignVCenter)
        self._add_btns: dict[str, CutButton] = {}
        for kind, text, icon in MARKER_KINDS:
            button = CutButton(text, "ghost", marker_bar, icon=icon, height=28, compact=True)
            button.clicked.connect(lambda _c=False, k=kind: self._add_step(k))
            marker_row.addWidget(button)
            self._add_btns[kind] = button
        marker_row.addStretch(1)
        marker_row.addWidget(Label(tr("timeline.actions.hint"), "small"), 0, Qt.AlignmentFlag.AlignVCenter)
        self._axis_field = FieldText(
            [tr("timeline.view.axis", name=tr("timeline.defaults.unnamed_axis"))], marker_bar
        )
        marker_row.addWidget(self._axis_field, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(marker_bar)

        # 工具栏 B：编辑 / 运行 / 文件
        toolbar_b = QWidget(host)
        toolbar_b.setFixedHeight(30)
        bar_b = QHBoxLayout(toolbar_b)
        bar_b.setContentsMargins(0, 0, 0, 0)
        bar_b.setSpacing(6)
        self._run_btn = CutButton(tr("timeline.view.start_run"), "primary", toolbar_b, icon="play", height=30, compact=True)
        self._run_btn.clicked.connect(self._toggle_run)
        bar_b.addWidget(self._run_btn)
        self._undo_btn = IconButton("refresh", tr("timeline.view.tips.undo"), toolbar_b, size=30, icon_size=14)
        self._undo_btn.clicked.connect(self._undo)
        bar_b.addWidget(self._undo_btn)
        self._redo_btn = IconButton("refresh", tr("timeline.view.tips.redo"), toolbar_b, size=30, icon_size=14)
        self._redo_btn.clicked.connect(self._redo)
        bar_b.addWidget(self._redo_btn)
        self._left_btn = IconButton("chevron-left", tr("timeline.view.tips.move_left"), toolbar_b, size=30, icon_size=14)
        self._left_btn.clicked.connect(lambda: self._move_selected(-1))
        bar_b.addWidget(self._left_btn)
        self._right_btn = IconButton("chevron-right", tr("timeline.view.tips.move_right"), toolbar_b, size=30, icon_size=14)
        self._right_btn.clicked.connect(lambda: self._move_selected(1))
        bar_b.addWidget(self._right_btn)
        self._delete_btn = IconButton("trash", tr("timeline.view.tips.delete"), toolbar_b, size=30, icon_size=14)
        self._delete_btn.clicked.connect(self._delete_selected)
        bar_b.addWidget(self._delete_btn)
        self._clear_btn = CutButton(tr("timeline.view.clear_sequence"), "ghost", toolbar_b, height=30, compact=True)
        self._clear_btn.clicked.connect(self._clear_steps)
        bar_b.addWidget(self._clear_btn)
        bar_b.addStretch(1)
        for text, icon, slot in (
            (tr("timeline.view.buttons.overlay"), "hud", self._open_run_settings),
            (tr("timeline.view.buttons.keys"), "settings", self._open_keys),
            (tr("timeline.view.buttons.library"), "list", self._open_library),
            (tr("timeline.view.buttons.share"), "link", self._open_share),
        ):
            button = CutButton(text, "ghost", toolbar_b, icon=icon, height=30, compact=True)
            button.clicked.connect(slot)
            bar_b.addWidget(button)
        self._save_btn = CutButton(tr("timeline.common.save"), "ghost", toolbar_b, icon="save", height=30, compact=True)
        self._save_btn.clicked.connect(self._save)
        bar_b.addWidget(self._save_btn)
        layout.addWidget(toolbar_b)

        # 序列
        seq_head = QWidget(host)
        seq_head.setFixedHeight(18)
        seq_row = QHBoxLayout(seq_head)
        seq_row.setContentsMargins(0, 0, 0, 0)
        seq_row.setSpacing(8)
        seq_row.addWidget(Label(tr("timeline.view.sequence.eyebrow"), "eyebrow"), 0)
        seq_row.addStretch(1)
        seq_row.addWidget(
            Label(tr("timeline.view.sequence.hint"), "small"),
            0,
        )
        layout.addWidget(seq_head)

        self._canvas = SequenceCanvas(service, self._cache, host)
        self._seq_scroll = QScrollArea(host)
        self._seq_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._seq_scroll.setWidgetResizable(False)
        self._seq_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._seq_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._seq_scroll.setWidget(self._canvas)
        self._seq_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self._seq_scroll.viewport().setAutoFillBackground(False)
        layout.addWidget(self._seq_scroll, 1)

        self._canvas.selection_changed.connect(self._on_selection)
        self._canvas.label_edit_requested.connect(self._edit_label)
        self._canvas.changed.connect(self._on_canvas_changed)
        self._team_slots.slot_clicked.connect(self._open_pool)
        self._team_slots.active_changed.connect(self._on_target_slot)

        # 统计
        stats = QWidget(host)
        stats.setFixedHeight(52)
        stats_row = QHBoxLayout(stats)
        stats_row.setContentsMargins(0, 0, 0, 0)
        stats_row.setSpacing(18)
        self._stat_steps = NumberStat(tr("timeline.view.stats.steps"), "—", stats, size=24, unit=tr("timeline.view.stats.unit_step"))
        self._stat_segments = NumberStat(tr("timeline.kind.segment"), "—", stats, size=24, unit=tr("timeline.view.stats.unit_segment"))
        self._stat_loops = NumberStat(tr("timeline.view.stats.loops"), "—", stats, size=24, unit=tr("timeline.view.stats.unit_loop"))
        self._stat_duration = NumberStat(tr("timeline.view.stats.duration"), "—", stats, size=24, unit=tr("timeline.view.stats.unit_second"))
        self._stat_energy = NumberStat(tr("timeline.view.stats.energy"), "—", stats, size=24, unit=tr("timeline.view.stats.unit_energy"))
        self._stat_team = NumberStat(tr("timeline.view.team.title"), "—", stats, size=24, unit="/4")
        for stat in (self._stat_steps, self._stat_segments, self._stat_loops,
                     self._stat_duration, self._stat_energy, self._stat_team):
            stat.setMinimumWidth(104)
            stats_row.addWidget(stat, 0)
        stats_row.addStretch(1)
        self._kinds_field = FieldText(["—"], stats)
        stats_row.addWidget(self._kinds_field, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addWidget(stats)

        layout.addWidget(Hairline(host))
        footer = QWidget(host)
        footer.setFixedHeight(22)
        footer_row = QHBoxLayout(footer)
        footer_row.setContentsMargins(0, 0, 0, 0)
        footer_row.setSpacing(10)
        footer_row.addWidget(Label(tr("timeline.view.notes.eyebrow"), "eyebrow"), 0)
        footer_row.addWidget(Label(tr("timeline.view.notes.text"), "small"), 0)
        footer_row.addStretch(1)
        self._path_field = FieldText([tr("timeline.view.path", path=service.path)], footer)
        footer_row.addWidget(self._path_field, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(footer)

        self._root.addWidget(host)
        self._pool = OperatorPool(service.assets, self._cache, self)
        self._pool.chosen.connect(self._choose_operator)
        self._pool.closed.connect(self._focus_canvas)
        self._state_sub = self.ctx.bus.subscribe(Topics.TIMELINE_STATE, self._on_state)
        self._run_sub = self.ctx.bus.subscribe("timeline.run", self._on_run)
        self.refresh()
        QTimer.singleShot(0, self._fit_canvas)

    # -- 服务 ---------------------------------------------------------------
    def _service(self):
        return getattr(self.ctx.services, "timeline", None)

    def _focus_canvas(self) -> None:
        if self._canvas is not None:
            self._canvas.setFocus()

    # -- 同步 ---------------------------------------------------------------
    def refresh(self) -> None:
        service = self._service()
        if service is None or self._canvas is None:
            return
        self._sync_all()

    def _sync_all(self) -> None:
        service = self._service()
        if service is None or self._canvas is None:
            return
        self._sync_team(service)
        self._sync_detail(service)
        self._sync_toolbar(service)
        self._sync_canvas(service)
        self._sync_stats(service)
        self._sync_warn(service)
        self._refresh_topbar()
        self._fit_canvas()

    def _sync_team(self, service) -> None:
        colors = pal()
        slots = []
        for name in service.team.to_list():
            operator = service.assets.get(name) if name else None
            skills = {}
            if operator is not None:
                for kind in ("skill", "link", "ult"):
                    info = service.assets.skill(operator.name, kind)
                    if info is not None and info.name:
                        skills[kind] = info.name
            if operator is None:
                slots.append({"name": "", "class": "", "element": "", "avatar": None, "avatar_small": None,
                              "class_icon": None, "element_icon": None, "rarity": None, "skills": {}})
            else:
                extra = service.assets.extra_for(name)
                slots.append({
                    "name": operator.name,
                    "class": operator.class_label,
                    "element": operator.element_label,
                    "avatar": self._cache.avatar(operator.avatar, 40),
                    "avatar_small": self._cache.avatar(operator.avatar, 18),
                    "class_icon": self._cache.icon(operator.class_icon, 11, colors.ink2),
                    "element_icon": self._cache.icon(operator.element_icon, 11),
                    "rarity": extra.rarity if extra is not None else None,
                    "skills": skills,
                })
        self._team_slots.set_data(slots, self._target_slot)
        self._actions.set_data(
            [{"name": item["name"], "avatar": item["avatar_small"], "skills": item["skills"]} for item in slots],
            service.keys(),
            self._target_slot,
        )
        self._team_fields.set_fields([
            tr("timeline.view.team.slots"),
            tr("timeline.view.team.pool_count", count=service.assets.count),
            tr("timeline.view.team.extra_available") if service.assets.extra_available else tr("timeline.view.team.extra_unavailable"),
        ])
        self._update_target_field(service)

    def _sync_detail(self, service) -> None:
        selection = self._canvas.selected_steps() if self._canvas is not None else []
        block = next((step for step in selection if step.is_block), None)
        index = int(block.operator) if block is not None else self._target_slot
        name = service.team.name(index)
        operator = service.assets.get(name) if name else None
        skills = service.assets.skills_for(name) if name else ()
        self._detail.set_operator(
            operator,
            service.assets.extra_for(name) if name else None,
            index,
            source=str(service.assets.extra_meta.get("source") or ""),
            skills=skills,
            step=block,
            duration=service.estimate_duration(block.kind, name, block.duration) if block is not None else 0.0,
        )

    def _update_target_field(self, service) -> None:
        name = service.axis_name or tr("timeline.defaults.unnamed_axis")
        self._axis_field.set_fields([tr("timeline.view.axis", name=name)])

    def _sync_toolbar(self, service) -> None:
        running = bool(service.run_active)
        self._run_btn.setText(tr("timeline.view.stop_run") if running else tr("timeline.view.start_run"))
        self._run_btn.set_variant("ghost" if running else "primary")
        self._run_btn.set_icon("stop" if running else "play")
        if not running:
            name = service.axis_name or tr("timeline.defaults.unnamed_axis")
            self._axis_field.set_fields([tr("timeline.view.axis", name=name)])

    def _sync_canvas(self, service) -> None:
        if self._canvas is None:
            return
        self._canvas.selection = [step for step in self._canvas.selection
                                  if any(item is step for item in service.model.steps)]
        self._canvas.relayout(self._seq_scroll.viewport().width() if hasattr(self, "_seq_scroll") else self.width())
        self._canvas.update()

    def _sync_stats(self, service) -> None:
        model = service.model
        counts = model.counts()
        stats = service.stats()
        self._stat_steps.set_value(str(len(model.steps)))
        self._stat_segments.set_value(str(len(model.segments())))
        self._stat_loops.set_value(str(len(model.loops())))
        self._stat_duration.set_value(f"{stats['total_duration']:.1f}")
        self._stat_energy.set_value(str(stats["energy_cost"]))
        self._stat_team.set_value(str(service.team.filled()))
        kinds = " · ".join(
            f"{tr(f'timeline.kind_short.{kind}')} {counts.get(kind, 0)}"
            for kind in ("skill", "link", "ult", "switch", "heavy", "comment", "break", "segment", "loop_start")
        )
        if stats["duration_missing"]:
            kinds += " · " + tr("timeline.view.stats.estimated_hint", count=stats["duration_missing"])
        self._kinds_field.set_fields([kinds])

    def _sync_warn(self, service) -> None:
        message = service.last_error or ""
        if message:
            self._warn.set_text(message)
            self._warn.show()
        else:
            self._warn.hide()

    def _refresh_topbar(self, *, force: bool = False) -> None:
        service = self._service()
        if service is None:
            return
        fields = [
            tr("timeline.view.topbar.steps", count=len(service.model.steps)),
            tr("timeline.view.topbar.segments", count=len(service.model.segments())),
            tr("timeline.view.topbar.loops", count=len(service.model.loops())),
            tr("timeline.view.topbar.team", filled=service.team.filled()),
        ]
        if service.run_active:
            state = self._run_state_cache or {}
            fields.append(tr("timeline.view.topbar.running", progress=state.get("progress_text") or "0/0"))
        if not force and fields == getattr(self, "_topbar_cache", None):
            return
        self._topbar_cache = fields
        window = getattr(self.ctx, "main_window", None)
        if window is None:
            return
        try:
            if window.stack.currentWidget() is self:
                window.topbar.set_fields(fields)
        except Exception:  # noqa: BLE001
            pass

    # -- 事件订阅 -----------------------------------------------------------
    def _on_state(self, state=None, **_kwargs) -> None:
        try:
            self._sync_all()
        except Exception:  # noqa: BLE001
            pass

    def _on_run(self, state=None, **_kwargs) -> None:
        state = dict(state or {})
        previous = self._run_state_cache or {}
        self._run_state_cache = state
        active = bool(state.get("active"))
        was_active = bool(previous.get("active"))
        service = self._service()
        if service is not None:
            self._sync_toolbar(service)
        if state.get("error"):
            self.ctx.notify(str(state.get("error")), "warn")
            self._refresh_topbar(force=True)
            return
        if state.get("admin") is False and active and not state.get("finished"):
            self.ctx.notify(str(state.get("admin_hint") or tr("timeline.view.notify.admin_hint")), "warn")
        if not active and was_active:
            reason = str(state.get("exit_reason") or "")
            if reason == "completed":
                self.ctx.notify(tr("timeline.view.notify.run_completed"), "ok")
            elif reason in ("F8", "view"):
                self.ctx.notify(tr("timeline.view.notify.run_exited"), "info")
            self._restore_window_after_run()
        self._refresh_topbar(force=True)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._service() is None or not hasattr(self, "_canvas"):
            return
        try:
            self._sync_all()
            self._refresh_topbar(force=True)
        except Exception:  # noqa: BLE001
            pass

    def _ensure_tick_subscription(self) -> None:
        return

    def on_show(self) -> None:
        if self._service() is None:
            return
        self._sync_all()
        self._refresh_topbar(force=True)
        self._focus_canvas()

    def on_hide(self) -> None:
        if self._pool is not None:
            self._pool.hide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._pool is not None and self._pool.isVisible():
            self._position_pool()
        if self._canvas is not None:
            QTimer.singleShot(0, self._fit_canvas)

    def _fit_canvas(self) -> None:
        if self._canvas is None or not hasattr(self, "_seq_scroll"):
            return
        width = max(320, self._seq_scroll.viewport().width())
        self._canvas.setFixedWidth(width)
        content = int(self._canvas.relayout(width))
        # 窗口高度不够时让序列区收缩，避免压到下方统计行（布局总高不足时 Qt 会重叠）。
        available = max(140, self.height() - self._chrome_height())
        self._seq_scroll.setFixedHeight(max(140, min(content, 260, available)))

    def _chrome_height(self) -> int:
        """除序列区以外的固定高度（队伍/详情/动作面板/工具条/统计/页脚 + 间距）。"""
        host = self._seq_scroll.parentWidget() if hasattr(self, "_seq_scroll") else None
        if host is None or host.layout() is None:
            return 500
        layout = host.layout()
        chrome = 0
        visible = 0
        for index in range(layout.count()):
            item = layout.itemAt(index)
            widget = item.widget()
            if widget is self._seq_scroll:
                continue
            if widget is not None and widget.isHidden():
                continue
            visible += 1
            if widget is not None:
                chrome += int(widget.height() or widget.sizeHint().height())
            else:
                chrome += int(item.sizeHint().height())
        chrome += int(layout.spacing()) * max(0, visible - 1)
        margins = layout.contentsMargins()
        chrome += int(margins.top()) + int(margins.bottom())
        return chrome

    def refresh_theme(self) -> None:
        if getattr(self, "_cache", None) is not None:
            self._cache.clear()
        if getattr(self, "_canvas", None) is not None:
            try:
                self._sync_all()
            except Exception:  # noqa: BLE001
                pass
        self.update()

    # -- 顶栏 ---------------------------------------------------------------
    def topbar_fields(self) -> list[str]:
        service = self._service()
        if service is None:
            return [tr("timeline.view.topbar.module_missing")]
        fields = [
            tr("timeline.view.topbar.steps", count=len(service.model.steps)),
            tr("timeline.view.topbar.segments", count=len(service.model.segments())),
            tr("timeline.view.topbar.loops", count=len(service.model.loops())),
        ]
        if service.run_active:
            progress = (self._run_state_cache or {}).get("progress_text") or "0/0"
            fields.append(tr("timeline.view.topbar.running", progress=progress))
        return fields

    def topbar_actions(self) -> list[QWidget]:
        return []

    # -- 编队 ---------------------------------------------------------------
    def _on_target_slot(self, index: int) -> None:
        self._target_slot = int(max(0, min(SLOT_COUNT - 1, index)))
        if self._canvas is not None:
            for step in self._canvas.selected_steps():
                if step.is_block:
                    self._service().update_step(step, operator=self._target_slot)
        service = self._service()
        if service is not None:
            self._sync_team(service)
            self._sync_detail(service)

    def _open_pool(self, index: int) -> None:
        service = self._service()
        if self._pool is None or service is None:
            return
        if not service.assets.available:
            self.ctx.notify(tr("timeline.view.notify.no_assets_pick"), "warn")
            return
        if self._pool.isVisible() and self._target_slot == index:
            self._pool.close_pool()
            return
        self._target_slot = int(max(0, min(SLOT_COUNT - 1, index)))
        self._on_target_slot(self._target_slot)
        self._pool.set_target(self._target_slot, service.team.name(self._target_slot))
        self._position_pool()
        self._pool.show()
        self._pool.raise_()

    def _quick_team(self) -> None:
        service = self._service()
        if service is None:
            return
        empty = [i for i, name in enumerate(service.team.to_list()) if not name]
        self._open_pool(empty[0] if empty else self._target_slot)

    def _position_pool(self) -> None:
        if self._pool is None:
            return
        width = min(380, max(320, self.width() - 40))
        height = min(max(320, self.height() - 12), 640)
        self._pool.setFixedSize(width, height)
        self._pool.move(max(0, self.width() - width - 6), 6)

    def _choose_operator(self, name: str) -> None:
        service = self._service()
        if service is None:
            return
        slots = service.team.to_list()
        if name in slots and slots.index(name) != self._target_slot:
            slots[slots.index(name)] = slots[self._target_slot]
        slots[self._target_slot] = name
        service.set_team(slots)
        for step in self._canvas.selected_steps() if self._canvas is not None else []:
            if step.is_block:
                service.update_step(step, operator=self._target_slot)
        self._pool.close_pool()
        self.ctx.notify(tr("timeline.view.notify.operator_joined", name=name, slot=self._target_slot + 1), "ok")
        self._focus_canvas()

    def _clear_team(self) -> None:
        service = self._service()
        if service is None:
            return
        if any(service.team.to_list()) and not confirm(
            self, title=tr("timeline.confirm.clear_team.title"),
            message=tr("timeline.confirm.clear_team.message"),
            action_text=tr("timeline.confirm.clear_team.action"),
        ):
            return
        service.clear_team()
        self.ctx.notify(tr("timeline.view.notify.team_cleared"), "ok")

    def _open_team_library(self) -> None:
        service = self._service()
        if service is None:
            return
        dialog = TeamLibraryDialog(service, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._sync_all()

    # -- 编辑 ---------------------------------------------------------------
    def _insert_index(self) -> int:
        if self._canvas is not None and self._canvas.selection:
            indices = [i for i, step in enumerate(self._service().model.steps)
                       if any(step is item for item in self._canvas.selection)]
            if indices:
                return max(indices) + 1
        return len(self._service().model.steps)

    def _add_step(self, kind: str) -> None:
        service = self._service()
        if service is None:
            return
        if kind in BLOCK_KINDS and not service.assets.available:
            self.ctx.notify(tr("timeline.view.notify.no_assets_step"), "warn")
            return
        if kind in BLOCK_KINDS and not service.team.name(self._target_slot):
            self.ctx.notify(tr("timeline.view.notify.slot_empty", slot=self._target_slot + 1), "warn")
            self._open_pool(self._target_slot)
            return
        label = {
            "comment": tr("timeline.view.default_comment_label"),
            "segment": tr("timeline.view.default_segment_label"),
        }.get(kind, "")
        step = service.add_step(kind, self._target_slot, self._insert_index(), label=label)
        if self._canvas is not None:
            self._canvas.select_step(step)
        self._focus_canvas()

    def _insert_skill(self, kind: str, operator: int) -> None:
        """动作面板：点某干员的技能按钮 → 直接插入「该干员 · 技能」（重击无干员）。"""
        service = self._service()
        if service is None:
            return
        heavy = kind == "heavy"
        slot = 0 if heavy else int(max(0, min(SLOT_COUNT - 1, operator)))
        if kind in BLOCK_KINDS and not service.assets.available:
            self.ctx.notify(tr("timeline.view.notify.no_assets_step"), "warn")
            return
        if not heavy and not service.team.name(slot):
            self.ctx.notify(tr("timeline.view.notify.slot_empty", slot=slot + 1), "warn")
            self._open_pool(slot)
            return
        defaults = service.step_defaults(kind, slot)
        step = service.add_step(
            kind, slot, self._insert_index(),
            duration=defaults.get("duration", 0.0), skill=defaults.get("skill", ""),
        )
        if not heavy:
            self._target_slot = slot
        if self._canvas is not None:
            self._canvas.select_step(step)
        self._focus_canvas()

    def _on_empty_slot(self, kind: str, operator: int) -> None:
        self.ctx.notify(
            tr(
                "timeline.actions.disabled_tip",
                kind=tr(f"timeline.kind.{kind}"),
                slot=int(operator) + 1,
            ),
            "warn",
        )
        self._open_pool(int(operator))

    def _move_selected(self, delta: int) -> None:
        service = self._service()
        if service is None or self._canvas is None:
            return
        service.move_steps(self._canvas.selection, delta)

    def _delete_selected(self) -> None:
        if self._canvas is None:
            return
        count = len(self._canvas.selected_steps())
        if not count:
            self.ctx.notify(tr("timeline.view.notify.delete_need_selection"), "warn")
            return
        if not confirm(self, title=tr("timeline.confirm.delete_steps.title"),
                       message=tr("timeline.confirm.delete_steps.message", count=count),
                       action_text=tr("timeline.confirm.delete_steps.action")):
            return
        self._canvas.delete_selected()

    def _clear_steps(self) -> None:
        service = self._service()
        if service is None:
            return
        if not confirm(self, title=tr("timeline.confirm.clear_steps.title"),
                       message=tr("timeline.confirm.clear_steps.message"),
                       action_text=tr("timeline.confirm.clear_steps.action")):
            return
        count = service.clear_steps()
        self._canvas.clear_selection()
        self.ctx.notify(tr("timeline.view.notify.steps_cleared", count=count), "ok")

    def _undo(self) -> None:
        service = self._service()
        if service is not None and service.undo():
            self._sync_all()

    def _redo(self) -> None:
        service = self._service()
        if service is not None and service.redo():
            self._sync_all()

    def _edit_label(self, step) -> None:
        service = self._service()
        if service is None or step is None:
            return
        if step.is_key:
            self._edit_step_timing(step)
            return
        titles = {"comment": tr("timeline.view.edit_titles.comment"), "segment": tr("timeline.view.edit_titles.segment"), "loop_start": tr("timeline.view.edit_titles.loop_start"),
                  "loop_end": tr("timeline.view.edit_titles.loop_end")}
        count = step.loop_count if step.kind == "loop_start" else None
        dialog = TextPromptDialog(titles.get(step.kind, tr("timeline.view.edit_titles.default")), step.label, count=count, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updates = {"label": dialog.text_value}
        if step.kind == "loop_start" and dialog.count_value is not None:
            updates["loop_count"] = dialog.count_value
        service.update_step(step, **updates)

    def _edit_step_timing(self, step) -> None:
        """双击按键步骤：改预计耗时 / 技能名（默认值来自 operators.json）。"""
        service = self._service()
        if service is None:
            return
        name = service.team.name(step.operator)
        info = service.assets.skill(name, step.kind)
        dialog = StepTimingDialog(
            step,
            suggested_duration=service.estimate_duration(step.kind, name, step.duration),
            suggested_skill=info.name if info is not None else "",
            data_available=info is not None,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        service.update_step(step, duration=dialog.duration_value, skill=dialog.skill_value)

    def _on_selection(self) -> None:
        service = self._service()
        if service is not None:
            self._sync_detail(service)
            self._sync_stats(service)

    def _on_canvas_changed(self) -> None:
        service = self._service()
        if service is not None:
            self._sync_stats(service)
            self._sync_detail(service)
            self._refresh_topbar()

    def _save(self) -> None:
        service = self._service()
        if service is None:
            return
        if service.save():
            self.ctx.notify(tr("timeline.view.notify.saved", path=service.path), "ok")
        else:
            self.ctx.notify(service.last_error or tr("timeline.view.notify.save_failed"), "warn")

    # -- 运行态 -------------------------------------------------------------
    def _toggle_run(self) -> None:
        service = self._service()
        if service is None:
            return
        if service.run_active:
            service.stop_run("view")
            return
        if self._pool is not None:
            self._pool.hide()
        if not service.start_run():
            return
        state = service.run_state()
        if not state.get("input_available"):
            self.ctx.notify(state.get("input_status") or tr("timeline.view.notify.input_unavailable"), "warn")
        elif not state.get("admin", True):
            self.ctx.notify(str(state.get("admin_hint") or tr("timeline.view.notify.admin_hint_short")), "warn")
        else:
            self.ctx.notify(tr("timeline.view.notify.run_started"), "ok")
        if service.run_settings().get("minimize", True):
            window = getattr(self.ctx, "main_window", None)
            if window is not None and window.isVisible():
                self._minimized_for_run = True
                QTimer.singleShot(180, lambda: self._delayed_minimize(window))
        self._sync_toolbar(service)
        self._refresh_topbar(force=True)

    def _delayed_minimize(self, window) -> None:
        try:
            from PyQt6 import sip

            if not sip.isdeleted(window):
                window.showMinimized()
        except Exception:  # noqa: BLE001
            pass

    def _restore_window_after_run(self) -> None:
        if not self._minimized_for_run:
            return
        self._minimized_for_run = False
        window = getattr(self.ctx, "main_window", None)
        if window is None:
            return
        try:
            if window.isMinimized():
                window.showNormal()
            window.raise_()
            window.activateWindow()
        except Exception:  # noqa: BLE001
            pass

    def _open_run_settings(self) -> None:
        service = self._service()
        if service is None:
            return
        dialog = RunSettingsDialog(service, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.ctx.notify(tr("timeline.view.notify.run_settings_saved"), "ok")

    def _open_keys(self) -> None:
        service = self._service()
        if service is None:
            return
        dialog = KeyConfigDialog(service, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.ctx.notify(tr("timeline.view.notify.keys_saved"), "ok")

    # -- 轴库 / 分享码 ------------------------------------------------------
    def _open_library(self) -> None:
        service = self._service()
        if service is None:
            return
        dialog = AxisLibraryDialog(service, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.ctx.notify(
                tr(
                    "timeline.view.notify.axis_loaded",
                    name=service.axis_name or tr("timeline.defaults.unnamed_axis"),
                ),
                "ok",
            )
            self._sync_all()

    def _open_share(self) -> None:
        service = self._service()
        if service is None:
            return
        dialog = ShareDialog(service, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.ctx.notify(
                tr(
                    "timeline.view.notify.axis_imported",
                    name=service.axis_name or tr("timeline.defaults.unnamed_axis"),
                ),
                "ok",
            )
            self._sync_all()
