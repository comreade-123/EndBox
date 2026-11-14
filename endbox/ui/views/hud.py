# -*- coding: utf-8 -*-
"""HUD 控制视图：开关、实时预览、显示项、模式/位置、热键录制与状态。

预览用的画布和桌面上的覆盖层是**同一份** ``HudCanvas`` 绘制代码，
所以这里看到的就是真正会出现在游戏画面上的东西（数据、降级态、两种模式都一致）。
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from endbox.app_context import AppContext
from endbox.core.text import catalog, t
from endbox.modules.hud import hotkey as hotkey_mod
from endbox.modules.hud import state as S
from endbox.modules.hud.overlay import INTRO_MS, HudCanvas, design_size
from endbox.theme import fonts as F
from endbox.theme import paint
from endbox.ui.kit import (
    CutButton,
    EmptyState,
    FieldText,
    InfoFlag,
    Label,
    Panel,
    ScrollArea,
    Segmented,
    Toggle,
)
from endbox.ui.kit.base import EWidget, pal
from endbox.ui.views.base import BaseView


def _row(*widgets, spacing: int = 12) -> QWidget:
    host = QWidget()
    layout = QHBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for widget in widgets:
        if isinstance(widget, int):
            layout.addSpacing(widget)
        else:
            layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
    layout.addStretch(1)
    return host


class HudPreview(EWidget):
    """控制页里的预览：斜线纹充作游戏画面，上面叠真正的 HUD 画布。"""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self._ctx = ctx
        self.setFixedHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setToolTip(t("hud.preview.tooltip"))
        self.canvas = HudCanvas(self)
        self.canvas.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._snapshot: dict = {}
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(INTRO_MS)
        self._anim.valueChanged.connect(lambda value: self.canvas.set_progress(float(value)))
        self._anim.finished.connect(lambda: self.canvas.set_progress(1.0))

    def set_snapshot(self, snapshot: dict | None) -> None:
        self._snapshot = dict(snapshot or {})
        self.canvas.set_snapshot(self._snapshot)
        if self._anim.state() != QVariantAnimation.State.Running:
            self.canvas.set_progress(1.0)
        self._place_canvas()
        self.update()

    def play(self) -> None:
        if not bool(self._ctx.settings.get("app.animations", True)):
            self.canvas.set_progress(1.0)
            return
        self._anim.stop()
        self.canvas.set_progress(0.0)
        self._anim.start()

    def _place_canvas(self) -> None:
        margin = 18
        label_h = 18
        design = design_size(self.canvas.mode)
        available_w = max(1, self.width() - margin * 2)
        available_h = max(1, self.height() - margin * 2 - label_h)
        scale = min(available_w / design.width(), available_h / design.height())
        width = max(1, int(design.width() * scale))
        height = max(1, int(design.height() * scale))
        x = (self.width() - width) // 2
        y = label_h + margin + max(0, (available_h - height) // 2)
        self.canvas.setGeometry(x, y, width, height)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place_canvas()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.play()
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.play()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        p.fillRect(rect, colors.chip)
        paint.paint_hatch(p, QRectF(0, 0, self.width(), self.height()), colors.ink, alpha=0.08)
        p.setPen(colors.rule)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)
        paint.draw_text(
            p,
            QRectF(10, 4, self.width() - 20, 12),
            t("hud.preview.backdrop"),
            F.eyebrow_font(9),
            colors.ink2,
            elide=True,
        )
        if self.hasFocus():
            paint.paint_focus(p, rect.adjusted(1.5, 1.5, -1.5, -1.5), colors.trace)
        p.end()


class HotkeyRecorder(EWidget):
    """点击进入录制，按下组合键生效；取消 / 不支持时给出提示。"""

    started = pyqtSignal()
    recorded = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, spec: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(34)
        self.setMinimumWidth(230)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._spec = spec or S.MISSING
        self._recording = False
        self._hint = ""

    @property
    def recording(self) -> bool:
        return self._recording

    def set_spec(self, spec: str) -> None:
        self._spec = spec or S.MISSING
        self.update()

    def start(self) -> None:
        if self._recording:
            return
        self._recording = True
        self._hint = t("hud.hotkey_panel.recorder_recording")
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.update()
        self.started.emit()

    def stop(self) -> None:
        if not self._recording:
            return
        self._recording = False
        self._hint = ""
        self.update()

    def cancel(self) -> None:
        was = self._recording
        self.stop()
        if was:
            self.cancelled.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if not self._recording:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.cancel()
            event.accept()
            return
        if key in (
            Qt.Key.Key_Control,
            Qt.Key.Key_Shift,
            Qt.Key.Key_Alt,
            Qt.Key.Key_Meta,
            Qt.Key.Key_AltGr,
        ):
            event.accept()
            return
        spec = hotkey_mod.spec_from_qt(key, event.modifiers())
        if spec is None:
            self._hint = t("hud.hotkey_panel.recorder_unsupported")
            self.update()
            event.accept()
            return
        try:
            spec = hotkey_mod.normalize_hotkey(spec)
        except hotkey_mod.HotkeyError as exc:
            self._hint = str(exc)
            self.update()
            event.accept()
            return
        self.stop()
        self._spec = spec
        self.update()
        self.recorded.emit(spec)
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and not self._recording:
            self.start()
        super().mousePressEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
        if self._recording:
            # 点到别处 = 放弃录制，让服务恢复旧组合
            self._recording = False
            self._hint = ""
            self.update()
            self.cancelled.emit()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        p.fillPath(paint.cut_rect(rect, 8.0, "br"), colors.chip)
        p.strokePath(paint.cut_rect(rect, 8.0, "br"), colors.rule)
        if self._recording:
            p.fillRect(QRectF(0, 0, 4, self.height()), colors.signal)
            text = self._hint or t("hud.hotkey_panel.recorder_recording")
        else:
            text = t("hud.hotkey_panel.recorder", spec=self._spec)
        paint.draw_text(
            p,
            QRectF(12, 0, self.width() - 24, self.height()),
            text,
            F.font(12, cjk_first=True),
            colors.ink,
            elide=True,
        )
        if self.hasFocus() or self._recording:
            paint.paint_focus(p, rect.adjusted(1.5, 1.5, -1.5, -1.5), colors.trace)
        p.end()


class HudView(BaseView):
    key = "hud"
    eyebrow = t("hud.eyebrow")
    title = t("hud.view_title")
    icon = "hud"
    order = 30
    sidebar_label = t("hud.sidebar_label")
    description = t("hud.description")

    def build(self) -> None:
        self._service = self.ctx.services.hud
        self._snapshot: dict = self._service.snapshot() if self._service is not None else {}
        self._guard = False
        self._top_enable: CutButton | None = None
        self._top_preview: CutButton | None = None

        scroll = ScrollArea(self)
        self._scroll = scroll
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        # 记号带
        strip = QWidget()
        strip_layout = QHBoxLayout(strip)
        strip_layout.setContentsMargins(0, 0, 0, 0)
        strip_layout.setSpacing(12)
        strip_layout.addWidget(Label(t("hud.strip"), "eyebrow"))
        strip_layout.addStretch(1)
        self._strip_fields = FieldText(
            [t("hud.fields.mouse_through"), t("hud.fields.topmost"), t("hud.fields.no_focus")]
        )
        strip_layout.addWidget(self._strip_fields)
        layout.addWidget(strip)

        if self._service is None:
            layout.addWidget(
                EmptyState(
                    t("hud.empty.title"),
                    t("hud.empty.description"),
                    action=CutButton(t("hud.buttons.back_settings"), "ghost", icon="settings"),
                )
            )
            empty_action = layout.itemAt(layout.count() - 1).widget()
            if isinstance(empty_action, EmptyState) and empty_action._action is not None:  # noqa: SLF001
                empty_action._action.clicked.connect(lambda: self.ctx.open_view("settings"))  # noqa: SLF001
            layout.addWidget(self._help_panel())
            layout.addStretch(1)
            scroll.set_content(host)
            self._root.addWidget(scroll)
            return

        # 预览
        preview_panel = Panel(
            t("hud.preview.title"), t("hud.preview.eyebrow"), padding=16, spacing=10
        )
        self._preview = HudPreview(self.ctx, preview_panel)
        preview_panel.add(self._preview)
        self._enable_toggle = Toggle(t("hud.buttons.enable"), checked=self._service.enabled)
        self._enable_toggle.toggled.connect(self._on_enable_toggled)
        self._preview_btn = CutButton(t("hud.buttons.preview"), "primary", icon="eye")
        self._preview_btn.clicked.connect(self._on_preview)
        self._replay_btn = CutButton(t("hud.buttons.replay"), "ghost", icon="play")
        self._replay_btn.clicked.connect(self._on_replay)
        preview_panel.add(_row(self._enable_toggle, self._preview_btn, self._replay_btn))
        self._preview_fields = FieldText([t("hud.preview.source")])
        preview_panel.add(self._preview_fields)
        layout.addWidget(preview_panel)

        # 显示项
        fields_panel = Panel(t("hud.display.title"), t("hud.display.eyebrow"), padding=16, spacing=10)
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        self._show_toggles: dict[str, Toggle] = {}
        show_state = self._snapshot.get("show", {})
        for index, key in enumerate(S.SHOW_LABELS):
            toggle = Toggle(
                S.SHOW_LABELS[key],
                checked=bool(show_state.get(key, S.DEFAULT_SHOW.get(key, True))),
            )
            toggle.toggled.connect(lambda checked, k=key: self._on_show_toggled(k, checked))
            self._show_toggles[key] = toggle
            grid.addWidget(toggle, index // 2, index % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        fields_panel.add(grid_host)
        fields_panel.add(Label(t("hud.display.note"), "muted", wrap=True))
        layout.addWidget(fields_panel)

        # 模式与位置
        layout_panel = Panel(t("hud.layout.title"), t("hud.layout.eyebrow"), padding=16, spacing=12)
        self._mode_seg = Segmented(
            [S.MODE_LABELS[mode] for mode in S.MODES],
            index=S.mode_index(self._snapshot.get("mode", "compact")),
        )
        self._mode_seg.changed.connect(self._on_mode_changed)
        layout_panel.add(
            _row(
                Label(t("hud.layout.mode"), "body"),
                self._mode_seg,
                Label(t("hud.layout.mode_hint"), "small"),
            )
        )
        self._anchor_seg = Segmented(
            [S.ANCHOR_LABELS[anchor] for anchor in S.ANCHORS],
            index=S.anchor_index(self._snapshot.get("anchor", "top-center")),
        )
        self._anchor_seg.changed.connect(self._on_anchor_changed)
        layout_panel.add(_row(Label(t("hud.layout.anchor"), "body"), self._anchor_seg))

        self._offset_x = QSpinBox()
        self._offset_x.setRange(S.OFFSET_X_MIN, S.OFFSET_X_MAX)
        self._offset_x.setSingleStep(5)
        self._offset_x.setSuffix(" px")
        self._offset_x.valueChanged.connect(lambda value: self._set_option("offset_x", value))
        self._offset_y = QSpinBox()
        self._offset_y.setRange(S.OFFSET_Y_MIN, S.OFFSET_Y_MAX)
        self._offset_y.setSingleStep(5)
        self._offset_y.setSuffix(" px")
        self._offset_y.valueChanged.connect(lambda value: self._set_option("offset_y", value))
        layout_panel.add(
            _row(
                Label(t("hud.layout.offset_x"), "body"),
                self._offset_x,
                Label(t("hud.layout.offset_y"), "body"),
                self._offset_y,
            )
        )

        self._scale_label = Label("1.00×", "num")
        self._scale_label.setFixedWidth(64)
        scale_minus = CutButton("−", "ghost", compact=True, height=28)
        scale_minus.clicked.connect(lambda: self._bump("scale", -0.05))
        scale_plus = CutButton("+", "ghost", compact=True, height=28)
        scale_plus.clicked.connect(lambda: self._bump("scale", 0.05))
        scale_reset = CutButton(t("hud.layout.reset"), "ghost", compact=True, height=28)
        scale_reset.clicked.connect(lambda: self._set_option("scale", S.SCALE_DEFAULT))
        layout_panel.add(
            _row(Label(t("hud.layout.scale"), "body"), scale_minus, self._scale_label, scale_plus, scale_reset)
        )

        self._opacity_label = Label("96%", "num")
        self._opacity_label.setFixedWidth(64)
        opacity_minus = CutButton("−", "ghost", compact=True, height=28)
        opacity_minus.clicked.connect(lambda: self._bump("opacity", -0.04))
        opacity_plus = CutButton("+", "ghost", compact=True, height=28)
        opacity_plus.clicked.connect(lambda: self._bump("opacity", 0.04))
        opacity_reset = CutButton(t("hud.layout.reset"), "ghost", compact=True, height=28)
        opacity_reset.clicked.connect(lambda: self._set_option("opacity", S.OPACITY_DEFAULT))
        layout_panel.add(
            _row(
                Label(t("hud.layout.opacity"), "body"),
                opacity_minus,
                self._opacity_label,
                opacity_plus,
                opacity_reset,
            )
        )

        self._clock_seg = Segmented([t("hud.layout.clock_24"), t("hud.layout.clock_12")], index=0, height=28)
        self._clock_seg.changed.connect(self._on_clock_format_changed)
        self._clock_seconds = Toggle(t("hud.layout.clock_seconds"), checked=True)
        self._clock_seconds.toggled.connect(self._on_clock_seconds_toggled)
        layout_panel.add(_row(Label(t("hud.layout.clock_format"), "body"), self._clock_seg, self._clock_seconds))

        self._screen_combo = QComboBox()
        self._screens = self._service.available_screens()
        for screen in self._screens:
            suffix = t("hud.layout.screen_primary") if screen["primary"] else ""
            self._screen_combo.addItem(screen["label"] + suffix, screen["name"])
        if not self._screens:
            self._screen_combo.addItem(t("hud.layout.no_screen"), "")
            self._screen_combo.setEnabled(False)
        self._screen_combo.currentIndexChanged.connect(self._on_screen_changed)
        reset_btn = CutButton(t("hud.layout.reset_appearance"), "ghost", icon="refresh")
        reset_btn.clicked.connect(self._on_reset_appearance)
        layout_panel.add(_row(Label(t("hud.layout.screen"), "body"), self._screen_combo, reset_btn))
        layout_panel.add(Label(t("hud.layout.note"), "muted", wrap=True))
        layout.addWidget(layout_panel)

        # 热键
        hotkey_panel = Panel(
            t("hud.hotkey_panel.title"), t("hud.hotkey_panel.eyebrow"), padding=16, spacing=10
        )
        self._recorder = HotkeyRecorder(str(self._snapshot.get("hotkey", {}).get("spec") or S.MISSING))
        self._recorder.started.connect(self._on_record_started)
        self._recorder.recorded.connect(self._on_recorded)
        self._recorder.cancelled.connect(self._on_record_cancelled)
        self._hotkey_fields = FieldText([S.MISSING, S.MISSING])
        hotkey_panel.add(self._hotkey_fields)
        hotkey_panel.add(_row(self._recorder))
        self._hotkey_flag = InfoFlag("", kind="warn")
        self._hotkey_flag.setVisible(False)
        hotkey_panel.add(self._hotkey_flag)
        hotkey_panel.add(Label(t("hud.hotkey_panel.note"), "muted", wrap=True))
        layout.addWidget(hotkey_panel)

        # 状态
        status_panel = Panel(t("hud.status.title"), t("hud.status.eyebrow"), padding=16, spacing=10)
        self._status_rows = FieldText([S.MISSING])
        self._status_rows_2 = FieldText([S.MISSING])
        self._status_rows_3 = FieldText([S.MISSING])
        status_panel.add(self._status_rows)
        status_panel.add(self._status_rows_2)
        status_panel.add(self._status_rows_3)
        self._status_flag = InfoFlag("", kind="warn")
        self._status_flag.setVisible(False)
        status_panel.add(self._status_flag)
        layout.addWidget(status_panel)

        layout.addWidget(Label(t("hud.status.note_all"), "small", wrap=True))
        layout.addStretch(1)

        scroll.set_content(host)
        self._root.addWidget(scroll)

        self._service.changed.connect(self._on_service_changed)
        self.refresh()

    # ------------------------------------------------------------------
    # 顶栏
    # ------------------------------------------------------------------
    def topbar_fields(self) -> list[str]:
        if self._service is None:
            return []
        snapshot = self._current_snapshot()
        hotkey = snapshot.get("hotkey") or {}
        return [
            t("hud.topbar.hotkey", spec=hotkey.get("spec") or S.MISSING),
            S.MODE_LABELS[S.normalize_mode(snapshot.get("mode"))],
            t("hud.topbar.overlay_on" if snapshot.get("enabled") else "hud.topbar.overlay_off"),
            t("hud.topbar.scale", scale=f"{S.clamp_scale(snapshot.get('scale', 1.0)):.2f}"),
        ]

    def topbar_actions(self) -> list[QWidget]:
        if self._service is None:
            return []
        if self._top_enable is None:
            self._top_enable = CutButton(t("hud.buttons.top_enable"), "primary", icon="hud")
            self._top_enable.clicked.connect(self._toggle_from_topbar)
        if self._top_preview is None:
            self._top_preview = CutButton(t("hud.buttons.top_preview"), "ghost", icon="eye")
            self._top_preview.clicked.connect(self._on_preview)
        self._sync_topbar()
        return [self._top_enable, self._top_preview]

    def _toggle_from_topbar(self) -> None:
        if self._service is not None:
            self._service.toggle()

    def _sync_topbar(self) -> None:
        if self._top_enable is not None:
            enabled = bool(self._snapshot.get("enabled"))
            self._top_enable.setText(
                t("hud.buttons.top_disable") if enabled else t("hud.buttons.top_enable")
            )
            self._top_enable.set_variant("default" if enabled else "primary")

    # ------------------------------------------------------------------
    # 视图生命周期
    # ------------------------------------------------------------------
    def on_show(self) -> None:
        self.refresh()
        if hasattr(self, "_preview"):
            self._preview.play()
        if getattr(self, "_scroll", None) is not None:
            self._scroll.verticalScrollBar().setValue(0)

    def on_hide(self) -> None:
        # HUD 是后台服务，离开视图不停；这里只收尾预览动画与没结束的热键录制
        if hasattr(self, "_preview"):
            self._preview.canvas.set_progress(1.0)
        recorder = getattr(self, "_recorder", None)
        if recorder is not None and recorder.recording:
            recorder.cancel()

    def refresh(self) -> None:
        if self._service is None:
            return
        self._on_service_changed(self._service.snapshot())

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------
    def _current_snapshot(self) -> dict:
        if self._service is None:
            return self._snapshot or {}
        try:
            return self._service.snapshot()
        except Exception:  # noqa: BLE001
            return self._snapshot or {}

    def _on_service_changed(self, snapshot: dict) -> None:
        self._snapshot = dict(snapshot or {})
        self._guard = True
        try:
            self._apply(self._snapshot)
        finally:
            self._guard = False

    def _apply(self, snapshot: dict) -> None:
        if not hasattr(self, "_enable_toggle"):
            return
        enabled = bool(snapshot.get("enabled"))
        self._enable_toggle.setChecked(enabled)
        self._enable_toggle.update()
        self._mode_seg.set_index(S.mode_index(snapshot.get("mode")), emit=False)
        self._anchor_seg.set_index(S.anchor_index(snapshot.get("anchor")), emit=False)
        scale = S.clamp_scale(snapshot.get("scale", 1.0))
        opacity = S.clamp_opacity(snapshot.get("opacity", 0.96))
        self._scale_label.setText(f"{scale:.2f}×")
        self._opacity_label.setText(f"{int(round(opacity * 100))}%")
        self._offset_x.setValue(S.clamp_offset(snapshot.get("offset_x", 0), "x"))
        self._offset_y.setValue(S.clamp_offset(snapshot.get("offset_y", 0), "y"))
        self._clock_seg.set_index(1 if snapshot.get("clock_12h") else 0, emit=False)
        self._clock_seconds.setChecked(bool(snapshot.get("clock_seconds", True)))
        self._clock_seconds.update()
        show = snapshot.get("show") or {}
        for key, toggle in self._show_toggles.items():
            toggle.setChecked(bool(show.get(key, S.DEFAULT_SHOW.get(key, True))))
            toggle.update()
        screen_name = str(snapshot.get("screen") or "")
        index = self._screen_combo.findData(screen_name) if screen_name else -1
        if index < 0:
            index = next((i for i, item in enumerate(self._screens) if item["primary"]), 0)
        if index >= 0 and self._screen_combo.currentIndex() != index:
            self._screen_combo.setCurrentIndex(index)
        hotkey = snapshot.get("hotkey") or {}
        spec = hotkey.get("spec") or S.MISSING
        registered = bool(hotkey.get("registered"))
        self._recorder.set_spec(spec)
        self._hotkey_fields.set_fields(
            [
                t(
                    "hud.hotkey_panel.spec_registered" if registered else "hud.hotkey_panel.spec_unregistered",
                    spec=spec,
                )
            ]
        )
        error = hotkey.get("error")
        if error:
            self._hotkey_flag.set_text(t("hud.hotkey_panel.error", error=error))
            self._hotkey_flag.setVisible(True)
        else:
            self._hotkey_flag.setVisible(False)
        self._strip_fields.set_fields(
            [
                S.MODE_LABELS[S.normalize_mode(snapshot.get("mode"))],
                t("hud.fields.hotkey", spec=spec),
                t("hud.fields.mouse_through"),
                t("hud.fields.scale", scale=f"{scale:.2f}"),
            ]
        )
        self._preview_fields.set_fields(
            [
                t("hud.preview.mode", mode=S.MODE_LABELS[S.normalize_mode(snapshot.get("mode"))]),
                t(
                    "hud.preview.anchor",
                    anchor=S.ANCHOR_LABELS[S.normalize_anchor(snapshot.get("anchor"))],
                ),
                t(
                    "hud.preview.offset",
                    x=S.clamp_offset(snapshot.get("offset_x", 0), "x"),
                    y=S.clamp_offset(snapshot.get("offset_y", 0), "y"),
                ),
                t("hud.preview.opacity", opacity=int(round(opacity * 100))),
            ]
        )
        if hasattr(self, "_preview"):
            self._preview.set_snapshot(snapshot)
        self._apply_status(snapshot)
        self._sync_topbar()

    def _apply_status(self, snapshot: dict) -> None:
        account = snapshot.get("account") or {}
        power = snapshot.get("battery") or {}
        battery_view = S.battery_view(power)
        window_created = bool(snapshot.get("window_created"))
        window_visible = bool(snapshot.get("window_visible"))
        window_text = t(
            "hud.status.window_created" if window_created else "hud.status.window_not_created"
        )
        if window_created:
            window_text += t(
                "hud.status.window_visible" if window_visible else "hud.status.window_hidden"
            )
        mode = S.normalize_mode(snapshot.get("mode"))
        design = design_size(mode)
        scale = S.clamp_scale(snapshot.get("scale", 1.0))
        self._status_rows.set_fields(
            [
                t("hud.fields.window", state=window_text),
                t(
                    "hud.fields.design",
                    mode=S.MODE_LABELS[mode],
                    w=int(design.width() * scale),
                    h=int(design.height() * scale),
                ),
                t(
                    "hud.status.account_connected"
                    if account.get("connected")
                    else "hud.status.account_disconnected"
                ),
            ]
        )
        self._status_rows_2.set_fields(
            [
                t("hud.status.battery", detail=battery_view["detail_text"]),
                t("hud.status.clock", text=str(snapshot.get("clock") or S.MISSING)),
                t("hud.status.refresh"),
            ]
        )
        self._status_rows_3.set_fields(
            [
                t("hud.status.username", name=str(snapshot.get("username") or S.MISSING)),
                t(
                    "hud.status.hotkey",
                    spec=str((snapshot.get("hotkey") or {}).get("spec") or S.MISSING),
                ),
            ]
        )
        notes: list[str] = []
        if not account.get("connected"):
            notes.append(t("hud.status.note_account"))
        if not power.get("available"):
            notes.append(t("hud.status.note_battery"))
        if self._service is not None and not self._service.hotkey_registered:
            notes.append(t("hud.status.note_hotkey"))
        if notes:
            self._status_flag.set_text(" ".join(notes))
            self._status_flag.setVisible(True)
        else:
            self._status_flag.setVisible(False)

    # ------------------------------------------------------------------
    def _set_option(self, name: str, value) -> None:
        if self._guard or self._service is None:
            return
        self._service.set_option(name, value)

    def _bump(self, name: str, delta: float) -> None:
        if self._service is None:
            return
        snapshot = self._current_snapshot()
        current = snapshot.get(name)
        if name == "scale":
            self._service.set_option("scale", S.clamp_scale(S.clamp_scale(current) + delta))
        else:
            self._service.set_option("opacity", S.clamp_opacity(S.clamp_opacity(current) + delta))

    def _on_enable_toggled(self, checked: bool) -> None:
        if self._guard or self._service is None:
            return
        self._service.set_enabled(bool(checked))
        self.ctx.notify(t("hud.notify.enabled" if checked else "hud.notify.disabled"), "ok")

    def _on_preview(self) -> None:
        if self._service is None:
            return
        self._service.preview()
        if hasattr(self, "_preview"):
            self._preview.play()

    def _on_replay(self) -> None:
        if hasattr(self, "_preview"):
            self._preview.play()
        if self._service is not None and self._service.enabled:
            self._service.show()

    def _on_mode_changed(self, index: int) -> None:
        if self._guard or self._service is None:
            return
        mode = S.MODES[max(0, min(len(S.MODES) - 1, index))]
        self._service.set_option("mode", mode)

    def _on_anchor_changed(self, index: int) -> None:
        if self._guard or self._service is None:
            return
        anchor = S.ANCHORS[max(0, min(len(S.ANCHORS) - 1, index))]
        self._service.set_option("anchor", anchor)

    def _on_screen_changed(self, index: int) -> None:
        if self._guard or self._service is None or index < 0:
            return
        name = self._screen_combo.itemData(index) or ""
        self._service.set_option("screen", name)

    def _on_show_toggled(self, key: str, checked: bool) -> None:
        if self._guard or self._service is None:
            return
        self._service.set_option(key, bool(checked))

    def _on_clock_format_changed(self, index: int) -> None:
        if self._guard or self._service is None:
            return
        self._service.set_option("clock_12h", index == 1)

    def _on_clock_seconds_toggled(self, checked: bool) -> None:
        if self._guard or self._service is None:
            return
        self._service.set_option("clock_seconds", bool(checked))

    def _on_reset_appearance(self) -> None:
        if self._service is None:
            return
        self._service.set_option("mode", "compact")
        self._service.set_option("anchor", "top-center")
        self._service.set_option("offset_x", 0)
        self._service.set_option("offset_y", 0)
        self._service.set_option("scale", S.SCALE_DEFAULT)
        self._service.set_option("opacity", S.OPACITY_DEFAULT)
        self._service.set_option("screen", "")
        for key, default in S.DEFAULT_SHOW.items():
            self._service.set_option(key, default)
        self.ctx.notify(t("hud.notify.reset_done"), "ok")

    # -- 热键录制 -----------------------------------------------------------
    def _on_record_started(self) -> None:
        if self._service is None:
            return
        self._service.suspend_hotkey()
        self.ctx.notify(t("hud.notify.recording_start"), "info")

    def _on_recorded(self, spec: str) -> None:
        if self._service is None:
            return
        ok, message = self._service.set_hotkey(spec)
        if ok:
            self.ctx.notify(t("hud.notify.hotkey_updated", spec=self._service.hotkey_spec), "ok")
        else:
            self.ctx.notify(t("hud.notify.hotkey_failed", message=message), "warn")
        self.refresh()

    def _on_record_cancelled(self) -> None:
        if self._service is None:
            return
        self._service.resume_hotkey()
        self.ctx.notify(t("hud.notify.recording_cancelled"), "info")
        self.refresh()

    # ------------------------------------------------------------------
    def _help_panel(self) -> Panel:
        panel = Panel(t("hud.about.title"), t("hud.about.eyebrow"), padding=16, spacing=10)
        panel.add(Label(t("hud.about.body"), "muted", wrap=True))
        about = catalog.section("hud").get("about") or {}
        tags = about.get("tags") or []
        if tags:
            panel.add(FieldText([str(tag) for tag in tags]))
        return panel
