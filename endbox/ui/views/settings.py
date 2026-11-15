# -*- coding: utf-8 -*-
"""设置：外观、覆盖层、数据与凭据、模块与插件状态。

界面文案全部来自 assets/text/zh-CN.json 的 settings.* 分区。
"""
from __future__ import annotations

import sys
import time

from PyQt6.QtCore import QRectF, Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QPainter, QPixmap
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from endbox import __author__, config
from endbox.core.text import t
from endbox.core.workers import run_async
from endbox.modules.update import UpdateService
from endbox.theme import paint
from endbox.theme.tokens import theme
from endbox.ui.branding import logo_pixmap
from endbox.ui.kit import (
    BusyBar,
    CutButton,
    FieldText,
    InfoFlag,
    Label,
    Panel,
    ScrollArea,
    Segmented,
    Toggle,
)
from endbox.ui.kit.base import EWidget
from endbox.ui.views.base import BaseView
from endbox.version import GITHUB_RELEASES_URL, current_version, version_label


def _row(*widgets) -> QWidget:
    host = QWidget()
    layout = QHBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    for widget in widgets:
        layout.addWidget(widget)
    layout.addStretch(1)
    return host


def _open_path(path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


class _Logo(EWidget):
    """关于页的应用图标（用户提供的 icon.png）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(56, 56)
        self._pix = logo_pixmap(56)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(0, 0, self.width(), self.height())
        p.setClipPath(paint.cut_rect(rect, 10, "br"))
        p.drawPixmap(rect, self._pix, QRectF(self._pix.rect()))
        p.end()


class SettingsView(BaseView):
    key = "settings"
    eyebrow = "SETTINGS"
    title = "设置"
    icon = "settings"
    order = 90
    sidebar_label = t("shell.sidebar.labels.settings", "设置")
    description = "主题、覆盖层、数据目录与模块状态"

    def build(self) -> None:
        ctx = self.ctx
        scroll = ScrollArea(self)
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        # ------------------------------------------------------------ 外观
        appearance = Panel(
            t("settings.appearance.title", "外观"),
            t("settings.appearance.eyebrow", "APPEARANCE"),
            padding=16,
            spacing=12,
        )
        self._theme_seg = Segmented(
            [
                t("settings.appearance.theme_dark", "深色"),
                t("settings.appearance.theme_light", "浅色"),
            ],
            index=0 if theme.mode == "dark" else 1,
        )
        self._theme_seg.changed.connect(self._on_theme_seg)
        appearance.add(_row(Label(t("settings.appearance.theme", "主题"), "body"), self._theme_seg))
        self._anim_toggle = Toggle(
            t("settings.appearance.animations", "界面动效"),
            checked=bool(ctx.settings.get("app.animations", True)),
        )
        self._anim_toggle.clicked.connect(self._on_animations)
        appearance.add(_row(self._anim_toggle))
        appearance.add(Label(t("settings.appearance.note", ""), "muted", wrap=True))
        appearance.add(Label(t("settings.appearance.glass_note", ""), "muted", wrap=True))
        layout.addWidget(appearance)

        # ------------------------------------------------------------ HUD
        hud = Panel(
            t("settings.overlay.title", "HUD 覆盖层"),
            t("settings.overlay.eyebrow", "OVERLAY"),
            padding=16,
            spacing=12,
        )
        self._hud_toggle = Toggle(
            t("settings.overlay.enable", "启用悬浮覆盖层"),
            checked=bool(ctx.settings.get("hud.enabled", False)),
        )
        self._hud_toggle.clicked.connect(self._on_hud_toggle)
        hud.add(_row(self._hud_toggle))
        self._hud_fields = FieldText(
            [
                t("settings.overlay.hotkey", "热键 {spec}", spec=ctx.settings.get("hud.hotkey", "Ctrl+Alt+H")),
                t("settings.overlay.mouse_through", "鼠标穿透"),
                t("settings.overlay.borderless", "无边框置顶"),
            ]
        )
        hud.add(self._hud_fields)
        self._hud_flag = InfoFlag(t("settings.overlay.unregistered", "未注册"), kind="info")
        hud.add(self._hud_flag)
        hud.add(Label(t("settings.overlay.note", ""), "muted", wrap=True))
        layout.addWidget(hud)

        # ------------------------------------------------------------ 数据
        data = Panel(
            t("settings.data.title", "数据与凭据"),
            t("settings.data.eyebrow", "DATA"),
            padding=16,
            spacing=12,
        )
        data.add(
            FieldText(
                [
                    t("settings.data.dir", "数据目录 {path}", path=config.DATA_DIR),
                    t("settings.data.encrypted", "DPAPI 加密"),
                ]
            )
        )
        data_buttons = _row(
            CutButton(t("settings.data.open_data", "打开数据目录"), "default", icon="folder"),
            CutButton(t("settings.data.open_app", "打开软件目录"), "ghost", icon="external"),
            CutButton(t("settings.data.open_logs", "打开日志"), "ghost", icon="download"),
            CutButton(t("settings.data.open_plugins", "打开插件目录"), "ghost", icon="plugin"),
        )
        data.add(data_buttons)
        for button in data_buttons.findChildren(CutButton):
            if button.text() == t("settings.data.open_data", "打开数据目录"):
                button.clicked.connect(lambda: _open_path(config.DATA_DIR))
            elif button.text() == t("settings.data.open_app", "打开软件目录"):
                button.clicked.connect(lambda: _open_path(config.ROOT))
            elif button.text() == t("settings.data.open_logs", "打开日志"):
                button.clicked.connect(lambda: _open_path(config.LOG_DIR))
            elif button.text() == t("settings.data.open_plugins", "打开插件目录"):
                button.clicked.connect(lambda: _open_path(config.PLUGINS_DIR))
        data.add(Label(t("settings.data.note", ""), "muted", wrap=True))

        text_buttons = _row(
            CutButton(t("settings.data.open_texts", "打开文案文件"), "ghost", icon="external"),
            CutButton(t("settings.data.reload_texts", "重新加载文案"), "ghost", icon="refresh"),
        )
        data.add(text_buttons)
        data.add(Label(t("settings.data.texts_note", ""), "muted", wrap=True))
        for button in text_buttons.findChildren(CutButton):
            if button.text() == t("settings.data.open_texts", "打开文案文件"):
                button.clicked.connect(lambda: _open_path(config.ASSETS / "text" / "zh-CN.json"))
            elif button.text() == t("settings.data.reload_texts", "重新加载文案"):
                button.clicked.connect(self._reload_texts)
        layout.addWidget(data)

        # ------------------------------------------------------------ 模块
        modules = Panel(
            t("settings.modules.title", "模块与插件"),
            t("settings.modules.eyebrow", "MODULES"),
            padding=16,
            spacing=10,
        )
        self._module_fields = FieldText([t("common.placeholder", "—")])
        modules.add(self._module_fields)
        self._plugin_label = Label(t("settings.modules.plugins_missing", "插件宿主未加载"), "muted", wrap=True)
        modules.add(self._plugin_label)
        layout.addWidget(modules)

        # ------------------------------------------------------------ 关于
        about = Panel(
            t("settings.about.title", "关于"),
            t("settings.about.eyebrow", "ABOUT"),
            padding=16,
            spacing=10,
        )
        brand_row = QWidget()
        brand_layout = QHBoxLayout(brand_row)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(12)
        brand_layout.addWidget(_Logo(brand_row))
        title_box = QWidget()
        title_layout = QVBoxLayout(title_box)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(4)
        title_layout.addWidget(
            Label(
                t("settings.about.version", "EndBox · 终末地工具箱　v{version}", version=current_version()),
                "h2",
            )
        )
        title_layout.addWidget(Label(t("settings.about.author", "作者：{author}", author=__author__), "body"))
        title_layout.addWidget(Label(t("settings.about.disclaimer", ""), "small"))
        brand_layout.addWidget(title_box, 1)
        about.add(brand_row)
        about.add(Label(t("settings.about.desc", ""), "muted", wrap=True))
        about.add(
            FieldText(
                [t("settings.about.fields", "Python {python} │ PyQt6 │ Windows", python=sys.version.split()[0])]
            )
        )
        self._sponsor_btn = CutButton(t("settings.about.sponsor", "赞助支持"), "ghost", icon="spark")
        self._sponsor_btn.clicked.connect(self._show_sponsor)
        about.add(
            _row(
                self._sponsor_btn,
                Label(t("settings.about.sponsor_hint", "觉得好用可以请作者喝杯咖啡。"), "small"),
            )
        )
        layout.addWidget(about)

        # ------------------------------------------------------------ 更新
        update = Panel(
            t("update.title", "版本与更新"),
            t("update.eyebrow", "UPDATE"),
            padding=16,
            spacing=10,
        )
        self._update_service: UpdateService | None = getattr(ctx.services, "update", None)
        self._update_seq = 0
        self._update_running = False
        self._update_last: object | None = None
        self._update_fields = FieldText(
            [
                t("update.current", "当前版本 {version}", version=version_label()),
                t("update.never_checked"),
            ]
        )
        update.add(self._update_fields)

        update_buttons = _row(
            CutButton(t("update.check", "检查更新"), "primary", icon="refresh"),
            CutButton(t("update.release_page", "打开发布页"), "ghost", icon="external"),
            CutButton(t("update.ignore", "忽略此版本"), "ghost", icon="minus"),
        )
        update.add(update_buttons)
        (
            self._update_check_btn,
            self._update_page_btn,
            self._update_ignore_btn,
        ) = update_buttons.findChildren(CutButton)
        self._update_check_btn.clicked.connect(self._check_updates)
        self._update_page_btn.clicked.connect(self._open_release_page)
        self._update_ignore_btn.clicked.connect(self._toggle_ignore_version)
        self._update_ignore_btn.hide()

        self._update_busy = BusyBar()
        self._update_busy.hide()
        update.add(self._update_busy)
        self._update_flag = InfoFlag(t("update.status.idle", "还没有检查过更新。"), kind="info")
        update.add(self._update_flag)
        self._update_notes = Label(t("update.changelog_placeholder", ""), "muted", wrap=True)
        update.add(self._update_notes)
        update.add(Label(t("update.note", ""), "small", wrap=True))
        layout.addWidget(update)

        layout.addStretch(1)

        scroll.set_content(host)
        self._root.addWidget(scroll)
        self.refresh()

    # -- 行为 ---------------------------------------------------------------
    def _on_theme_seg(self, index: int) -> None:
        mode = "dark" if index == 0 else "light"
        if theme.mode != mode:
            theme.set_mode(mode)
            self.ctx.settings.set("app.theme", mode)
            self.ctx.notify(
                t("shell.theme_to_light" if mode == "light" else "shell.theme_to_dark"), "ok"
            )

    def _show_sponsor(self) -> None:
        """赞助码弹窗：扫码支持作者（图片随程序分发）。"""
        dialog = QDialog(self)
        dialog.setWindowTitle(t("settings.about.sponsor_title", "赞助支持"))
        dialog.setMinimumWidth(360)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        path = config.ASSETS / "branding" / "reward-code.png"
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            layout.addWidget(Label(t("settings.about.sponsor_missing", "赞助码图片缺失。"), "muted"))
        else:
            image = QLabel()
            image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            scaled = pixmap.scaled(
                280, 280,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            image.setPixmap(scaled)
            layout.addWidget(image, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(Label(t("settings.about.sponsor_note", ""), "small", wrap=True))
        close = CutButton(t("common.close", "关闭"), "primary")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        dialog.exec()

    def _reload_texts(self) -> None:
        from endbox.core.text import reload_texts

        if reload_texts():
            self.ctx.notify(t("settings.data.texts_reloaded", "文案已重新加载"), "ok")
        else:
            self.ctx.notify(t("settings.data.texts_failed", "文案加载失败，详见日志"), "warn")

    # -- 更新 ---------------------------------------------------------------
    def _update_service_instance(self) -> UpdateService:
        service = getattr(self, "_update_service", None)
        if service is None:
            service = UpdateService(settings=self.ctx.settings)
            self._update_service = service
        return service

    @staticmethod
    def _format_update_time(timestamp: float) -> str:
        if not timestamp:
            return t("update.never_checked")
        return time.strftime("%m-%d %H:%M", time.localtime(timestamp))

    def _refresh_update_summary(self) -> None:
        """只读缓存/设置，不发网络请求；切回设置页时恢复上次结果。"""
        if not hasattr(self, "_update_fields"):
            return
        service = self._update_service_instance()
        fields = [
            t("update.current", "当前版本 {version}", version=version_label()),
            t("update.latest", "最新版本 {version}", version="—"),
            t("update.checked_at", "上次检查 {time}", time=self._format_update_time(service.last_check_at())),
        ]
        ignored = service.ignored_version()
        if ignored:
            fields.append(t("update.ignored_line", "已忽略 {version}", version=f"v{ignored}"))
        self._update_fields.set_fields(fields)
        cached = service.cached_result()
        if cached is not None and cached.info is not None:
            self._render_update_result(cached)
        elif getattr(self, "_update_last", None) is None:
            self._update_flag.set_text(t("update.status.idle", "还没有检查过更新。"))
            self._update_ignore_btn.hide()
            self._update_notes.setText(t("update.changelog_placeholder", ""))

    def _render_update_result(self, result) -> None:
        if not hasattr(self, "_update_flag"):
            return
        self._update_last = result
        service = self._update_service_instance()
        current = str(getattr(result, "current", "") or current_version())
        info = getattr(result, "info", None)
        if not getattr(result, "ok", False) or info is None:
            self._update_fields.set_fields(
                [
                    t("update.current", "当前版本 {version}", version=f"v{current}"),
                    t(
                        "update.checked_at",
                        "上次检查 {time}",
                        time=self._format_update_time(getattr(result, "checked_at", 0) or 0),
                    ),
                ]
            )
            self._update_flag.set_text(
                str(getattr(result, "error", None) or t("update.status.failed", error="—"))
            )
            self._update_ignore_btn.hide()
            self._update_notes.setText(t("update.changelog_placeholder", ""))
            return

        ignored = bool(getattr(result, "ignored", False)) or service.is_ignored(info.version)
        has_update = info.is_newer_than(current) and not ignored
        cached = bool(getattr(result, "cached", False))
        source = str(getattr(info, "source", ""))
        source_label = (
            t("update.source.cache", "来源 离线缓存")
            if cached
            else (
                t("update.source.github", "来源 GitHub Releases")
                if source == "github"
                else t("update.source.version_json", "来源 version.json")
            )
        )
        fields = [
            t("update.current", "当前版本 {version}", version=f"v{current}"),
            t("update.latest", "最新版本 {version}", version=f"v{info.version}"),
            t(
                "update.checked_at",
                "上次检查 {time}",
                time=self._format_update_time(getattr(result, "checked_at", 0) or 0),
            ),
            source_label,
        ]
        if ignored:
            fields.append(t("update.ignored_line", "已忽略 {version}", version=f"v{info.version}"))
        self._update_fields.set_fields(fields)

        if ignored:
            text = t("update.status.ignored", "发现新版本 {version}，但你选择了忽略", version=f"v{info.version}")
        elif has_update:
            text = t(
                "update.status.available",
                "发现新版本 {version}（当前 {current}）",
                version=f"v{info.version}",
                current=f"v{current}",
            )
        else:
            text = t("update.status.up_to_date", "已是最新版本（{version}）", version=f"v{info.version}")
        if cached:
            text = t("update.status.cached", "网络不可用，显示上次检查结果。") + text
        self._update_flag.set_text(text)
        self._update_ignore_btn.setVisible(has_update or ignored)
        self._update_ignore_btn.setText(
            t("update.unignore", "取消忽略") if ignored else t("update.ignore", "忽略此版本")
        )
        notes = info.display_notes()
        if len(notes) > 900:
            notes = notes[:900] + "…"
        self._update_notes.setText(notes)

    def _check_updates(self) -> None:
        if getattr(self, "_update_running", False):
            return
        service = self._update_service_instance()
        self._update_running = True
        self._update_seq += 1
        seq = self._update_seq
        self._update_check_btn.setEnabled(False)
        self._update_busy.show()
        self._update_busy.start()
        self._update_flag.set_text(t("update.status.checking", "正在检查更新…"))

        def _finish() -> None:
            self._update_running = False
            self._update_check_btn.setEnabled(True)
            self._update_busy.stop()
            self._update_busy.hide()

        def _done(result) -> None:
            if seq != self._update_seq:
                return
            try:
                _finish()
                self._render_update_result(result)
            except RuntimeError:  # 视图已销毁
                pass

        def _failed(exc: Exception) -> None:
            if seq != self._update_seq:
                return
            try:
                _finish()
                self._update_flag.set_text(
                    t("update.status.failed", "检查更新失败：{error}", error=str(exc) or type(exc).__name__)
                )
            except RuntimeError:
                pass

        try:
            run_async(service.check, on_done=_done, on_error=_failed, force=True)
        except Exception as exc:  # noqa: BLE001 - 线程池异常也不能把设置页弄崩
            _finish()
            self._update_flag.set_text(
                t("update.status.failed", "检查更新失败：{error}", error=f"{type(exc).__name__}: {exc}")
            )

    def _open_release_page(self) -> None:
        url = ""
        result = getattr(self, "_update_last", None)
        info = getattr(result, "info", None) if result is not None else None
        if info is not None:
            url = info.best_download()[0] or info.html_url
        if not url:
            url = GITHUB_RELEASES_URL
        if not QDesktopServices.openUrl(QUrl(url)):
            self.ctx.notify(t("update.open_failed", "打开链接失败：{error}", error=url), "warn")

    def _toggle_ignore_version(self) -> None:
        service = self._update_service_instance()
        result = getattr(self, "_update_last", None)
        version = ""
        if result is not None and getattr(result, "info", None) is not None:
            version = result.info.version
        if not version:
            cached = service.cached_result()
            if cached is not None and cached.info is not None:
                version = cached.info.version
        if not version:
            return
        if service.is_ignored(version):
            service.unignore()
            self.ctx.notify(t("update.unignore_ok", "已取消忽略 {version}", version=f"v{version}"), "ok")
        else:
            service.ignore(version)
            self.ctx.notify(t("update.ignore_ok", "{version} 不会再提示，直到有更新的版本", version=f"v{version}"), "info")
        if result is not None:
            result.ignored = service.is_ignored(version)
            self._render_update_result(result)
        else:
            self._refresh_update_summary()

    def _on_animations(self) -> None:
        enabled = self._anim_toggle.isChecked()
        self.ctx.settings.set("app.animations", enabled)
        self.ctx.notify(
            t("settings.appearance.animations_on" if enabled else "settings.appearance.animations_off"),
            "ok",
        )

    def _on_hud_toggle(self) -> None:
        enabled = self._hud_toggle.isChecked()
        self.ctx.settings.set("hud.enabled", enabled)
        service = self.ctx.services.hud
        if service is not None and hasattr(service, "set_enabled"):
            try:
                service.set_enabled(enabled)
            except Exception:  # noqa: BLE001
                self.ctx.notify(t("common.unknown_error", "操作失败，请查看日志。"), "warn")
        self.ctx.notify(
            t("settings.overlay.enabled_on" if enabled else "settings.overlay.enabled_off"), "ok"
        )

    def refresh(self) -> None:
        if not hasattr(self, "_module_fields"):
            return
        snapshot = self.ctx.services.snapshot()
        names = {
            "account": t("settings.modules.account", "账号"),
            "timeline": t("settings.modules.timeline", "排轴"),
            "hud": t("settings.modules.hud", "覆盖层"),
            "plugins": t("settings.modules.plugins", "插件"),
        }
        self._module_fields.set_fields(
            [
                t("settings.modules.online", "在线 {name}", name=names[key])
                if ok
                else t("settings.modules.offline", "未载入 {name}", name=names[key])
                for key, ok in snapshot.items()
            ]
        )
        self._theme_seg.set_index(0 if theme.mode == "dark" else 1)
        plugins = self.ctx.services.plugins
        if plugins is not None:
            count = len(getattr(plugins, "loaded", []) or [])
            self._plugin_label.setText(
                t("settings.modules.plugins_running", "", count=count)
            )
        self._anim_toggle.setChecked(bool(self.ctx.settings.get("app.animations", True)))
        hud = self.ctx.services.hud
        enabled = bool(getattr(hud, "enabled", self.ctx.settings.get("hud.enabled", False)))
        self._hud_toggle.setChecked(enabled)
        if hud is not None and hasattr(hud, "snapshot"):
            try:
                snap = hud.snapshot()
                hotkey = snap.get("hotkey") or {}
                spec = hotkey.get("spec") or self.ctx.settings.get("hud.hotkey", "Ctrl+Alt+H")
                registered = bool(hotkey.get("registered"))
                self._hud_fields.set_fields(
                    [
                        t("settings.overlay.hotkey", "热键 {spec}", spec=spec),
                        t(
                            "settings.overlay.registered" if registered else "settings.overlay.unregistered",
                            "已注册" if registered else "未注册",
                        ),
                        t("settings.overlay.mouse_through", "鼠标穿透"),
                        t("settings.overlay.borderless", "无边框置顶"),
                    ]
                )
                if hotkey.get("error"):
                    self._hud_flag.set_text(
                        t("settings.overlay.flag_error", "热键注册失败：{error}", error=hotkey["error"])
                    )
                elif registered:
                    self._hud_flag.set_text(t("settings.overlay.flag_registered", "", spec=spec))
                else:
                    self._hud_flag.set_text(t("settings.overlay.flag_unregistered", ""))
            except Exception:  # noqa: BLE001
                self._hud_flag.set_text(t("settings.overlay.flag_read_failed", ""))
        else:
            self._hud_fields.set_fields(
                [
                    t(
                        "settings.overlay.hotkey",
                        "热键 {spec}",
                        spec=self.ctx.settings.get("hud.hotkey", "Ctrl+Alt+H"),
                    ),
                    t("settings.overlay.unloaded", "模块未载入"),
                ]
            )
            self._hud_flag.set_text(t("settings.overlay.flag_unloaded", ""))
        self._refresh_update_summary()
