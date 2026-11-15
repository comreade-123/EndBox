# -*- coding: utf-8 -*-
"""插件管理：「已安装 / 市场」双标签。

已安装：插件清单、状态、权限、错误、启用/禁用、重新加载、打开目录、卸载。
市场：官方源（随程序分发）+ 用户添加的第三方源；分类筛选、搜索、安装 / 更新 /
卸载、下载进度与错误提示。所有破坏性操作都走 confirm 确认框。

插件加载失败、manifest 损坏、权限越权都会在这里显示成人话；任何插件问题都不
影响主程序运行。市场只负责「下载 → 校验 → 解压落盘」，加载仍由宿主完成。
"""
from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtCore import QObject, QRectF, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QPainter
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from endbox import config
from endbox.core.log import get_logger
from endbox.core.text import t
from endbox.core.workers import debounce, run_async
from endbox.plugins.host import STATE_LOADED, PluginRecord
from endbox.plugins.market import (
    InstallResult,
    MarketEntry,
    Marketplace,
    MarketError,
)
from endbox.theme import paint
from endbox.ui.icons import has_icon, paint_icon
from endbox.version import current_version
from endbox.ui.kit import (
    Badge,
    BusyBar,
    CutButton,
    EmptyState,
    FieldText,
    Hairline,
    IconButton,
    InfoFlag,
    Label,
    Panel,
    ProgressBar,
    ScrollArea,
    Segmented,
    confirm,
)
from endbox.ui.kit.base import EWidget, pal
from endbox.ui.views.base import BaseView

log = get_logger("ui.plugins")


def _open_local(path: Path) -> bool:
    """用系统文件管理器打开目录；路径不存在时返回 False。"""
    target = Path(path)
    if not target.exists():
        return False
    return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))))


def _format_size(size: int) -> str:
    value = max(0, int(size or 0))
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.1f} MB"
    if value >= 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value} B"


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


class _ProgressBridge(QObject):
    """把后台线程的下载进度转成 Qt 信号（回到 UI 线程更新进度条）。"""

    progress = pyqtSignal(int, int)


class _IconTile(EWidget):
    """插件图标块：墨色=已加载，灰色=未加载（强调色留给状态条与操作）。"""

    def __init__(self, icon: str, active: bool = False, parent=None, *, size: int = 34):
        super().__init__(parent)
        self._icon = icon if has_icon(icon) else "plugin"
        self._active = active
        self.setFixedSize(size, size)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = pal()
        rect = QRectF(0, 0, self.width(), self.height())
        p.fillPath(paint.cut_rect(rect, 7, "br"), colors.chip)
        color = colors.ink if self._active else colors.ink2
        paint_icon(p, self._icon, rect.adjusted(8, 8, -8, -8), color, stroke=1.6)
        p.end()


class _PluginCard(EWidget):
    """一个已安装插件 = 一块细线板：名称/版本/作者/来源/权限/状态/错误/操作。"""

    def __init__(
        self,
        view: "PluginManagerView",
        record: PluginRecord,
        parent=None,
        *,
        origin: str = "",
        market_entry: MarketEntry | None = None,
    ):
        super().__init__(parent)
        self._view = view
        self._record = record
        self._origin = origin
        self._market_entry = market_entry
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(7)

        top = QHBoxLayout()
        top.setSpacing(10)
        icon = record.manifest.icon if record.manifest is not None else "plugin"
        top.addWidget(_IconTile(icon, record.state == STATE_LOADED), 0, Qt.AlignmentFlag.AlignTop)

        name_box = QVBoxLayout()
        name_box.setSpacing(1)
        name_box.addWidget(Label(record.name, "h2"))
        author = record.author
        meta = f"{record.plugin_id}　·　v{record.version}　·　{author}"
        name_box.addWidget(Label(meta, "small"))
        top.addLayout(name_box, 1)

        if market_entry is not None and market_entry.is_update_of(record.version):
            top.addWidget(
                Badge(
                    t(
                        "plugins.market.update_available",
                        "可更新到 v{version}",
                        version=market_entry.version,
                    )
                ),
                0,
                Qt.AlignmentFlag.AlignVCenter,
            )
        badge = Badge(record.state_label)
        top.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)

        reload_btn = IconButton("refresh", t("plugins.card.reload_tooltip"))
        reload_btn.clicked.connect(lambda: self._view._reload(record))
        top.addWidget(reload_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        open_btn = IconButton("folder", t("plugins.card.open_tooltip"))
        open_btn.clicked.connect(lambda: self._view._open_path(record.path))
        top.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        if record.source != "bundled":
            uninstall_btn = IconButton(
                "trash", t("plugins.card.uninstall_tooltip"), variant="danger"
            )
            uninstall_btn.clicked.connect(lambda: self._view._uninstall(record))
            top.addWidget(uninstall_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        toggle_text = (
            t("plugins.card.disable") if record.state == STATE_LOADED else t("plugins.card.enable")
        )
        toggle = CutButton(
            toggle_text, "ghost" if record.state == STATE_LOADED else "default", compact=True
        )
        toggle.clicked.connect(lambda: self._view._toggle(record))
        top.addWidget(toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(top)

        if record.manifest is not None and record.manifest.description:
            layout.addWidget(Label(record.manifest.description, "muted", wrap=True))

        entry = record.manifest.entry if record.manifest is not None else "—"
        source = (
            t("plugins.card.source.bundled")
            if record.source == "bundled"
            else t("plugins.card.source.user")
        )
        origin_label = self._view._origin_label(origin or record.source)
        when = (
            time.strftime("%H:%M:%S", time.localtime(record.registered_at))
            if record.registered_at
            else t("plugins.card.not_loaded")
        )
        fields = [
            t("plugins.card.field.source", source=source),
            t("plugins.card.field.entry", entry=entry),
            t("plugins.card.field.views", count=len(record.view_keys)),
            t("plugins.card.field.load", ms=f"{record.load_ms:.1f}"),
            t("plugins.card.field.recent", time=when),
        ]
        if origin and origin != record.source:
            fields.insert(1, t("plugins.card.origin", "来源 {origin}", origin=origin_label))
        if record.permissions:
            fields.append(t("plugins.card.permissions", permissions=" / ".join(record.permissions)))
        else:
            fields.append(t("plugins.card.no_permissions"))
        if record.ignored_permissions:
            fields.append(t("plugins.card.ignored", permissions=" / ".join(record.ignored_permissions)))
        layout.addWidget(FieldText(fields))

        labels = self._view._view_labels(record)
        if labels:
            layout.addWidget(
                Label(t("plugins.card.views_line", labels="、".join(labels)), "small", wrap=True)
            )

        if record.error:
            flag = InfoFlag(f"{record.state_label}：{record.error}", kind="warn")
            if record.detail:
                flag.setToolTip(record.detail[-1800:])
            layout.addWidget(flag)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = pal()
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        p.fillRect(rect, colors.panel_surface)
        p.setPen(colors.rule)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)
        # 黄色只给"需要注意"的插件（加载失败 / 有错误 / 有可用更新）
        highlight = bool(
            self._record.error
            or self._record.state != STATE_LOADED
            or (
                self._market_entry is not None
                and self._market_entry.is_update_of(self._record.version)
            )
        )
        if highlight:
            p.fillRect(QRectF(0, 0, 3, self.height()), colors.signal)
        p.end()


class _MarketCard(EWidget):
    """市场里的一个插件 = 名称/版本/作者/分类/大小/权限/描述 + 安装按钮 + 进度。"""

    def __init__(
        self,
        view: "PluginManagerView",
        entry: MarketEntry,
        parent=None,
        *,
        installed_version: str | None = None,
        origin: str = "",
    ):
        super().__init__(parent)
        self._view = view
        self._entry = entry
        self._installed_version = installed_version
        self._origin = origin
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._busy = False
        self._error = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(7)

        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(_IconTile(entry.icon, installed_version is not None), 0, Qt.AlignmentFlag.AlignTop)
        name_box = QVBoxLayout()
        name_box.setSpacing(1)
        name_box.addWidget(Label(entry.name, "h2"))
        meta = f"v{entry.version}"
        if entry.author:
            meta += f"　·　{entry.author}"
        if entry.category:
            meta += f"　·　{entry.category}"
        name_box.addWidget(Label(meta, "small"))
        top.addLayout(name_box, 1)

        self._updatable = bool(installed_version and entry.is_update_of(installed_version))
        top.addWidget(Badge(entry.source_name or t("plugins.market.source.third_party")), 0, Qt.AlignmentFlag.AlignVCenter)

        self._action = CutButton(
            self._action_text(), "primary" if self._action_enabled() else "ghost", compact=True
        )
        self._action.clicked.connect(lambda: self._view._install_entry(entry))
        top.addWidget(self._action, 0, Qt.AlignmentFlag.AlignVCenter)
        if entry.homepage:
            home_btn = IconButton("external", t("plugins.market.card.homepage"))
            home_btn.clicked.connect(lambda: self._open_homepage(entry))
            top.addWidget(home_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(top)

        if entry.description:
            layout.addWidget(Label(entry.description, "muted", wrap=True))

        fields = [
            t("plugins.market.card.size", "大小 {size}", size=_format_size(entry.size)),
            t(
                "plugins.market.card.min_version",
                "最低版本 {version}",
                version=f"v{entry.min_app_version}" if entry.min_app_version else "—",
            ),
            t(
                "plugins.market.card.permissions",
                "权限 {permissions}",
                permissions=" / ".join(entry.permissions)
                if entry.permissions
                else t("plugins.market.card.no_permissions", "无需权限"),
            ),
        ]
        if entry.tags:
            fields.append(entry.tags_text)
        layout.addWidget(FieldText(fields))

        self._progress = ProgressBar()
        self._progress.hide()
        self._status = Label("", "small")
        self._status.hide()
        self._flag = InfoFlag("", kind="warn")
        self._flag.hide()
        layout.addWidget(self._progress)
        layout.addWidget(self._status)
        layout.addWidget(self._flag)

    # -- 状态 ---------------------------------------------------------------
    def _action_text(self) -> str:
        entry = self._entry
        if not entry.compatible():
            return t(
                "plugins.market.card.incompatible",
                "需要 EndBox {version}+",
                version=entry.min_app_version,
            )
        if self._installed_version is None:
            return t("plugins.market.card.install", "安装")
        if self._updatable:
            if self._origin == "bundled":
                return t("plugins.market.card.bundled_newer", "等程序更新")
            return t("plugins.market.card.update", "更新到 v{version}", version=entry.version)
        return t("plugins.market.card.installed", "已安装")

    def _action_enabled(self) -> bool:
        if not self._entry.compatible():
            return False
        if self._installed_version is None:
            return True
        if self._updatable:
            return self._origin != "bundled"
        return False

    def set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        self._action.setEnabled(not busy and self._action_enabled())
        if busy:
            self._progress.show()
            self._status.setText(text or t("plugins.market.card.installing", "安装中…"))
            self._status.show()
            self._flag.hide()
        else:
            self._progress.hide()
            self._status.hide()

    def set_progress(self, done: int, total: int) -> None:
        if total > 0:
            percent = int(done * 100 / total)
            self._progress.set_ratio(done / total)
            self._status.setText(
                t("plugins.market.card.downloading", "下载中 {percent}%", percent=percent)
            )
        else:
            self._progress.set_ratio(0.0)
            self._status.setText(
                t("plugins.market.card.downloaded", "已下载 {size}", size=_format_size(done))
            )
        self._status.show()

    def set_error(self, message: str) -> None:
        self._error = message
        self.set_busy(False)
        self._flag.set_text(
            t("plugins.market.card.failed", "安装失败：{error}", error=message)
        )
        self._flag.show()

    def refresh_installed(self, installed_version: str | None, origin: str) -> None:
        self._installed_version = installed_version
        self._origin = origin
        self._updatable = bool(installed_version and self._entry.is_update_of(installed_version))
        self._action.setText(self._action_text())
        self._action.setEnabled(self._action_enabled() and not self._busy)
        self.update()

    # -- 交互 ---------------------------------------------------------------
    def _open_homepage(self, entry: MarketEntry) -> None:
        if entry.homepage and not QDesktopServices.openUrl(QUrl(entry.homepage)):
            self._view.ctx.notify(
                t("plugins.market.notify.open_failed", "打开链接失败：{url}", url=entry.homepage),
                "warn",
            )

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = pal()
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        p.fillRect(rect, colors.panel_surface)
        p.setPen(colors.rule)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)
        if self._updatable or not self._entry.compatible():
            p.fillRect(QRectF(0, 0, 3, self.height()), colors.signal)
        p.end()


class PluginManagerView(BaseView):
    key = "plugins"
    eyebrow = "PLUGINS"
    title = t("plugins.title")
    icon = "plugin"
    order = 80
    sidebar_label = t("plugins.sidebar_label")
    description = t("plugins.description")

    def build(self) -> None:
        self._refresh_pending = False
        self._market_entries: list[MarketEntry] = []
        self._market_errors: list[str] = []
        self._market_loading = False
        self._market_loaded = False
        self._market_cards: dict[str, _MarketCard] = {}
        self._category_names: list[str] = []
        self._selected_category = ""
        self._installing: set[str] = set()
        self._bridges: list[_ProgressBridge] = []
        self._marketplace = Marketplace(settings=self.ctx.settings)
        self._bootstrap_official_index()

        # 顶部：标签 + 汇总
        head = QWidget()
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(0, 0, 0, 0)
        head_layout.setSpacing(10)
        self._tabs = Segmented(
            [t("plugins.tab.installed", "已安装"), t("plugins.tab.market", "插件市场")],
            index=0,
        )
        self._tabs.changed.connect(self._on_tab_changed)
        head_layout.addWidget(self._tabs)
        self._summary = FieldText(["—"])
        head_layout.addWidget(self._summary, 0, Qt.AlignmentFlag.AlignVCenter)
        head_layout.addStretch(1)
        self._build_btn = CutButton(
            t("plugins.toolbar.market_shortcut", "打开市场"), "ghost", icon="grid"
        )
        self._build_btn.clicked.connect(lambda: self._tabs.set_index(1))
        head_layout.addWidget(self._build_btn)
        self._root.addWidget(head)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_installed_page())
        self._stack.addWidget(self._build_market_page())
        self._root.addWidget(self._stack, 1)

        self.refresh()

    # ------------------------------------------------------------------
    # 已安装页
    # ------------------------------------------------------------------
    def _build_installed_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        scroll = ScrollArea(self)
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        bar = QWidget()
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(8)
        self._rescan_btn = CutButton(t("plugins.toolbar.rescan"), "primary", icon="refresh")
        self._rescan_btn.clicked.connect(self._rescan)
        bar_layout.addWidget(self._rescan_btn)
        open_bundled = CutButton(t("plugins.toolbar.open_bundled"), "ghost", icon="folder")
        open_bundled.clicked.connect(lambda: self._open_path(config.PLUGINS_DIR))
        bar_layout.addWidget(open_bundled)
        open_user = CutButton(t("plugins.toolbar.open_user"), "ghost", icon="folder")
        open_user.clicked.connect(lambda: self._open_path(config.USER_PLUGINS_DIR))
        bar_layout.addWidget(open_user)
        bar_layout.addStretch(1)
        self._count_field = FieldText(["—"])
        bar_layout.addWidget(self._count_field, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(bar)

        layout.addWidget(InfoFlag(t("plugins.notice"), kind="info"))

        self._missing = InfoFlag(t("plugins.missing"), kind="warn")
        self._missing.hide()
        layout.addWidget(self._missing)

        self._error_flag = InfoFlag("", kind="warn")
        self._error_flag.hide()
        layout.addWidget(self._error_flag)

        self._list_host = QWidget()
        self._list_host.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(8)
        layout.addWidget(self._list_host)

        empty_action = CutButton(t("plugins.empty.action"), "default", icon="folder")
        empty_action.clicked.connect(lambda: self._open_path(config.PLUGINS_DIR))
        self._empty = EmptyState(
            t("plugins.empty.title"),
            t("plugins.empty.desc"),
            action=empty_action,
        )
        layout.addWidget(self._empty)

        contract = Panel(t("plugins.contract.title"), "CONTRACT", padding=14, spacing=8)
        contract.add(Label(t("plugins.contract.permissions"), "muted", wrap=True))
        contract.add(Label(t("plugins.contract.events"), "muted", wrap=True))
        contract.add(Label(t("plugins.contract.builtin", path=config.PLUGINS_DIR), "small", wrap=True))
        contract.add(Label(t("plugins.contract.user", path=config.USER_PLUGINS_DIR), "small", wrap=True))
        layout.addWidget(contract)
        layout.addStretch(1)
        scroll.set_content(host)
        page_layout.addWidget(scroll)
        return page

    # ------------------------------------------------------------------
    # 市场页
    # ------------------------------------------------------------------
    def _build_market_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        scroll = ScrollArea(self)
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        layout.addWidget(InfoFlag(t("plugins.market_notice"), kind="info"))

        bar = QWidget()
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(8)
        self._market_refresh_btn = CutButton(
            t("plugins.toolbar.refresh_market", "刷新市场"), "primary", icon="refresh"
        )
        self._market_refresh_btn.clicked.connect(lambda: self._load_market(force=True))
        bar_layout.addWidget(self._market_refresh_btn)
        self._sources_btn = CutButton(
            t("plugins.toolbar.manage_sources", "插件源"), "ghost", icon="link"
        )
        self._sources_btn.clicked.connect(self._toggle_sources)
        bar_layout.addWidget(self._sources_btn)
        self._search = QLineEdit()
        self._search.setPlaceholderText(t("plugins.market.search_placeholder"))
        self._search.setFixedHeight(32)
        self._search.textChanged.connect(self._on_search_changed)
        bar_layout.addWidget(self._search, 1)
        layout.addWidget(bar)

        self._market_busy = BusyBar()
        self._market_busy.hide()
        layout.addWidget(self._market_busy)
        self._market_flag = InfoFlag(t("plugins.market.idle"), kind="info")
        layout.addWidget(self._market_flag)
        self._market_host_missing = InfoFlag(t("plugins.market.host_missing"), kind="warn")
        self._market_host_missing.hide()
        layout.addWidget(self._market_host_missing)

        self._category_host = QWidget()
        self._category_layout = QHBoxLayout(self._category_host)
        self._category_layout.setContentsMargins(0, 0, 0, 0)
        self._category_layout.setSpacing(8)
        layout.addWidget(self._category_host)

        self._market_list_host = QWidget()
        self._market_list_host.setStyleSheet("background: transparent;")
        self._market_list_layout = QVBoxLayout(self._market_list_host)
        self._market_list_layout.setContentsMargins(0, 0, 0, 0)
        self._market_list_layout.setSpacing(8)
        layout.addWidget(self._market_list_host)

        self._market_empty = EmptyState(
            t("plugins.market.empty.title", "这个源还没有插件。"),
            t("plugins.market.empty.desc", "添加一个第三方插件源，或等官方源更新后再来看看。"),
        )
        layout.addWidget(self._market_empty)
        self._market_no_match = Label(
            t("plugins.market.no_match", "没有匹配的插件：换个关键词，或切到「全部」分类。"),
            "muted",
            wrap=True,
        )
        self._market_no_match.hide()
        layout.addWidget(self._market_no_match)

        # 插件源面板（默认隐藏）
        self._sources_panel = Panel(
            t("plugins.market.sources.title", "插件源"), "SOURCES", padding=16, spacing=10
        )
        self._sources_panel.hide()
        self._official_source = Label("", "small", wrap=True)
        self._sources_panel.add(self._official_source)
        self._sources_rows_host = QWidget()
        self._sources_layout = QVBoxLayout(self._sources_rows_host)
        self._sources_layout.setContentsMargins(0, 0, 0, 0)
        self._sources_layout.setSpacing(6)
        self._sources_panel.add(self._sources_rows_host)
        self._sources_empty = Label(
            t(
                "plugins.market.sources.empty",
                "还没有第三方源。官方源随程序分发，不需要手动添加。",
            ),
            "muted",
            wrap=True,
        )
        self._sources_panel.add(self._sources_empty)
        add_row = QWidget()
        add_layout = QHBoxLayout(add_row)
        add_layout.setContentsMargins(0, 0, 0, 0)
        add_layout.setSpacing(8)
        self._source_name = QLineEdit()
        self._source_name.setPlaceholderText(t("plugins.market.sources.name_placeholder"))
        self._source_name.setFixedHeight(32)
        self._source_name.setMaximumWidth(200)
        add_layout.addWidget(self._source_name, 0)
        self._source_url = QLineEdit()
        self._source_url.setPlaceholderText(t("plugins.market.sources.url_placeholder"))
        self._source_url.setFixedHeight(32)
        add_layout.addWidget(self._source_url, 1)
        add_btn = CutButton(t("plugins.market.sources.add", "添加源"), "default", icon="plus")
        add_btn.clicked.connect(self._add_source)
        add_layout.addWidget(add_btn)
        self._sources_panel.add(add_row)
        self._sources_panel.add(Label(t("plugins.market.sources.note"), "small", wrap=True))
        # 面板放在工具栏下面（之前放在市场列表末尾，点开后在页面最底部，看起来像"没反应"）
        layout.insertWidget(2, self._sources_panel)

        layout.addStretch(1)
        scroll.set_content(host)
        page_layout.addWidget(scroll)
        self._refresh_sources_ui()
        return page

    # ------------------------------------------------------------------
    # 数据 / 刷新
    # ------------------------------------------------------------------
    def _host(self):
        host = self.ctx.services.plugins
        return host if hasattr(host, "plugins") else None

    def _bootstrap_official_index(self) -> None:
        """启动时先同步读仓库内官方索引（本地文件，不联网），摘要和市场页立即有内容。"""
        try:
            index = self._marketplace.official_index()
        except MarketError:
            return
        self._market_entries = list(index.entries)

    def _view_labels(self, record: PluginRecord) -> list[str]:
        host = self._host()
        labels: list[str] = []
        if host is None:
            return labels
        for key in record.view_keys:
            view_cls = host.registry.get(key)
            label = getattr(view_cls, "sidebar_label", "") or getattr(view_cls, "title", "") or key
            labels.append(label)
        return labels

    def _origin_label(self, origin: str) -> str:
        if origin == "market":
            return t("plugins.market.origin.market", "市场安装")
        if origin == "bundled":
            return t("plugins.market.origin.bundled", "随程序分发")
        return t("plugins.market.origin.local", "本地安装")

    def _installed_versions(self) -> dict[str, str]:
        host = self._host()
        if host is None:
            return {}
        return {record.plugin_id: record.version for record in host.plugins}

    def _market_entry_map(self) -> dict[str, MarketEntry]:
        return {entry.id: entry for entry in self._market_entries}

    def refresh(self) -> None:
        self._refresh_installed()
        self._refresh_market_cards()
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        host = self._host()
        plugins = len(host.plugins) if host is not None else 0
        self._summary.set_fields(
            [
                t("plugins.market.stats", "市场 {count} 个插件 │ 已安装 {installed} 个",
                  count=len(self._market_entries), installed=plugins)
            ]
        )

    def _refresh_installed(self) -> None:
        if not hasattr(self, "_list_layout"):
            return
        host = self._host()
        _clear_layout(self._list_layout)

        if host is None:
            self._missing.show()
            self._empty.hide()
            self._error_flag.hide()
            self._count_field.set_fields([t("plugins.count.host_missing")])
            return

        self._missing.hide()
        records = host.plugins
        summary = host.summary()
        self._count_field.set_fields(
            [
                t("plugins.count.loaded", count=summary["loaded"]),
                t("plugins.count.total", count=summary["total"]),
                t("plugins.count.disabled", count=summary["disabled"]),
                t("plugins.count.errors", count=summary["error"]),
                t("plugins.count.scan", ms=f"{host.last_scan_ms:.1f}"),
            ]
        )
        if summary["error"]:
            self._error_flag.set_text(t("plugins.error.summary", count=summary["error"]))
            self._error_flag.show()
        else:
            self._error_flag.hide()

        if not records:
            self._empty.show()
        else:
            self._empty.hide()
            entries = self._market_entry_map()
            for record in records:
                origin = self._marketplace.origin_of(record.plugin_id)
                self._list_layout.addWidget(
                    _PluginCard(
                        self,
                        record,
                        origin=origin,
                        market_entry=entries.get(record.plugin_id),
                    )
                )

    def _on_tab_changed(self, index: int) -> None:
        self._stack.setCurrentIndex(max(0, min(1, index)))
        self._build_btn.setVisible(index == 0)
        if index == 1 and not self._market_loaded:
            self._load_market(force=False)

    def _load_market(self, *, force: bool) -> None:
        if self._market_loading:
            return
        self._market_loading = True
        self._market_refresh_btn.setEnabled(False)
        self._market_busy.show()
        self._market_busy.start()
        self._market_flag.set_text(
            t("plugins.market.loading", "正在加载市场索引…")
        )
        self._market_flag.show()

        def _done(catalog) -> None:
            self._market_loading = False
            self._market_refresh_btn.setEnabled(True)
            self._market_busy.stop()
            self._market_busy.hide()
            self._market_loaded = True
            self._market_entries = list(getattr(catalog, "entries", []) or [])
            self._market_errors = list(getattr(catalog, "errors", []) or [])
            self._render_market()
            self._refresh_market_cards()
            self._refresh_summary()
            if self._market_errors:
                self._market_flag.set_text(
                    t("plugins.market.offline", "有插件源加载失败（可能离线或限流），已使用缓存/官方源。")
                )
            elif any(index.cached for index in getattr(catalog, "indexes", [])):
                self._market_flag.set_text(
                    t("plugins.market.cached", "部分插件源来自离线缓存，点「刷新市场」可以重试。")
                )
            else:
                self._market_flag.set_text(
                    t(
                        "plugins.market.loaded",
                        "已加载 {count} 个插件（官方源 + {sources} 个第三方源）。",
                        count=len(self._market_entries),
                        sources=max(0, len(getattr(catalog, "indexes", [])) - 1),
                    )
                )

        def _failed(exc: Exception) -> None:
            self._market_loading = False
            self._market_refresh_btn.setEnabled(True)
            self._market_busy.stop()
            self._market_busy.hide()
            self._market_loaded = True
            self._market_entries = []
            self._market_errors = [str(exc)]
            self._render_market()
            self._market_flag.set_text(
                t("plugins.market.error.load", "市场索引加载失败：{error}", error=exc)
            )

        try:
            run_async(self._marketplace.load_catalog, on_done=_done, on_error=_failed, force=force)
        except Exception as exc:  # noqa: BLE001
            _failed(exc)

    def _on_search_changed(self, _text: str) -> None:
        self._render_market()

    def _on_category_changed(self, index: int) -> None:
        categories = getattr(self, "_category_names", [])
        self._selected_category = categories[index - 1] if 0 < index <= len(categories) else ""
        self._render_market()

    def _categories(self) -> list[str]:
        found: list[str] = []
        for entry in self._market_entries:
            category = entry.category.strip()
            if category and category not in found:
                found.append(category)
        return found

    def _render_market(self) -> None:
        if not hasattr(self, "_market_list_layout"):
            return
        categories = self._categories()
        if categories != getattr(self, "_category_names", None):
            self._category_names = categories
            self._selected_category = ""
            _clear_layout(self._category_layout)
            self._category_seg = Segmented(
                [t("plugins.market.category.all", "全部")] + categories
            )
            self._category_seg.changed.connect(self._on_category_changed)
            self._category_layout.addWidget(self._category_seg)
            self._category_layout.addStretch(1)
        selected = getattr(self, "_selected_category", "")
        keyword = self._search.text().strip().lower()

        filtered: list[MarketEntry] = []
        for entry in self._market_entries:
            if selected and entry.category != selected:
                continue
            if keyword:
                haystack = " ".join(
                    [
                        entry.id,
                        entry.name,
                        entry.description,
                        entry.author,
                        entry.category,
                        " ".join(entry.tags),
                        entry.changelog,
                    ]
                ).lower()
                if keyword not in haystack:
                    continue
            filtered.append(entry)

        _clear_layout(self._market_list_layout)
        self._market_cards = {}
        installed = self._installed_versions()
        for entry in filtered:
            installed_version = installed.get(entry.id)
            origin = self._marketplace.origin_of(entry.id) if installed_version else ""
            card = _MarketCard(
                self,
                entry,
                installed_version=installed_version,
                origin=origin,
            )
            self._market_list_layout.addWidget(card)
            self._market_cards[entry.id] = card

        total = len(self._market_entries)
        if total == 0:
            self._market_empty.show()
            self._market_no_match.hide()
        elif not filtered:
            self._market_empty.hide()
            self._market_no_match.show()
        else:
            self._market_empty.hide()
            self._market_no_match.hide()

    def _refresh_market_cards(self) -> None:
        if not hasattr(self, "_market_cards") or not self._market_cards:
            return
        installed = self._installed_versions()
        for plugin_id, card in self._market_cards.items():
            version = installed.get(plugin_id)
            origin = self._marketplace.origin_of(plugin_id) if version else ""
            card.refresh_installed(version, origin)

    # ------------------------------------------------------------------
    # 安装 / 卸载
    # ------------------------------------------------------------------
    def _install_entry(self, entry: MarketEntry) -> None:
        if entry.id in self._installing:
            self.ctx.notify(t("plugins.market.error.busy", "上一个安装任务还没结束，请稍候。"), "info")
            return
        host = self._host()
        installed = self._installed_versions().get(entry.id)
        origin = self._marketplace.origin_of(entry.id) if installed else ""
        if installed and origin == "bundled" and entry.is_update_of(installed):
            self.ctx.notify(
                t("plugins.market.error.bundled", "随程序分发的插件不能卸载。"),
                "info",
            )
            return
        if not entry.compatible():
            self.ctx.notify(
                t(
                    "plugins.market.error.incompatible",
                    "这个插件需要 EndBox {version} 或更高（当前 {current}）。",
                    version=entry.min_app_version,
                    current=current_version(),
                ),
                "warn",
            )
            return
        if installed:
            if entry.is_update_of(installed):
                ok = confirm(
                    self,
                    title=t("plugins.market.confirm.update_title", "更新插件"),
                    message=t(
                        "plugins.market.confirm.update_message",
                        "把 {name} 从 v{old} 更新到 v{new}？插件会被重新加载。",
                        name=entry.name,
                        old=installed,
                        new=entry.version,
                    ),
                    action_text=t("plugins.market.confirm.update_action", "更新"),
                    danger=False,
                )
            else:
                ok = confirm(
                    self,
                    title=t("plugins.market.confirm.cover_title", "覆盖安装插件"),
                    message=t(
                        "plugins.market.confirm.cover_message",
                        "已经存在同 ID 的插件 {id}，继续会覆盖它的文件。确定吗？",
                        id=entry.id,
                    ),
                    action_text=t("plugins.market.confirm.cover_action", "覆盖安装"),
                    danger=False,
                )
        else:
            ok = confirm(
                self,
                title=t("plugins.market.confirm.install_title", "安装插件"),
                message=t(
                    "plugins.market.confirm.install_message",
                    "安装 {name} v{version}？来源：{source}。安装后插件会被加载。",
                    name=entry.name,
                    version=entry.version,
                    source=entry.source_name or "—",
                ),
                action_text=t("plugins.market.confirm.install_action", "安装"),
                danger=False,
            )
        if not ok:
            return

        self._installing.add(entry.id)
        card = self._market_cards.get(entry.id)
        if card is not None:
            card.set_busy(True)

        # 注意：宿主是 Qt 对象，卸载/加载必须留在 UI 线程。
        # 这里先在 UI 线程卸下旧版本，后台线程只做下载 + 解压 + 落盘。
        host = self._host()
        had_record = host.get(entry.id) if host is not None else None
        if host is not None and had_record is not None:
            try:
                host.disable(entry.id)
            except Exception:  # noqa: BLE001 - 取消失败也不阻塞安装
                log.exception("disable before market install failed: %s", entry.id)

        def _restore_host(ok: bool) -> None:
            if host is None:
                return
            try:
                if had_record is not None:
                    host.enable(entry.id)
                elif ok:
                    host.rescan()
            except Exception:  # noqa: BLE001 - 宿主刷新失败不影响磁盘上的安装结果
                log.exception("plugin host refresh after market install failed: %s", entry.id)

        bridge = _ProgressBridge(self)
        bridge.progress.connect(
            lambda done, total, pid=entry.id: self._on_install_progress(pid, done, total)
        )
        self._bridges.append(bridge)

        def _done(result: InstallResult) -> None:
            self._installing.discard(entry.id)
            self._discard_bridge(bridge)
            _restore_host(result.ok)
            if result.ok:
                if installed:
                    self.ctx.notify(
                        t(
                            "plugins.market.notify.updated",
                            "已更新 {name} 到 v{version}",
                            name=entry.name,
                            version=result.version,
                        ),
                        "ok",
                    )
                else:
                    self.ctx.notify(
                        t(
                            "plugins.market.notify.installed",
                            "已安装 {name} v{version}",
                            name=entry.name,
                            version=result.version,
                        ),
                        "ok",
                    )
            else:
                message = result.error or t("plugins.market.error.generic", "操作失败：{error}", error="—")
                self.ctx.notify(
                    t("plugins.market.notify.install_failed", "安装失败：{error}", error=message),
                    "warn",
                )
                current = self._market_cards.get(entry.id)
                if current is not None:
                    current.set_error(message)
            self.refresh()

        def _failed(exc: Exception) -> None:
            self._installing.discard(entry.id)
            self._discard_bridge(bridge)
            _restore_host(False)
            current = self._market_cards.get(entry.id)
            if current is not None:
                current.set_error(str(exc))
            self.ctx.notify(
                t("plugins.market.notify.install_failed", "安装失败：{error}", error=exc), "warn"
            )
            self.refresh()

        def _work() -> InstallResult:
            return self._marketplace.install(entry, progress=bridge.progress.emit)

        try:
            run_async(_work, on_done=_done, on_error=_failed)
        except Exception as exc:  # noqa: BLE001
            _failed(exc)

    def _on_install_progress(self, plugin_id: str, done: int, total: int) -> None:
        card = self._market_cards.get(plugin_id)
        if card is not None:
            card.set_progress(done, total)

    def _discard_bridge(self, bridge: _ProgressBridge) -> None:
        try:
            self._bridges.remove(bridge)
        except ValueError:
            pass
        bridge.deleteLater()

    def _uninstall(self, record: PluginRecord) -> None:
        if record.source == "bundled":
            self.ctx.notify(t("plugins.market.error.bundled", "随程序分发的插件不能卸载。"), "info")
            return
        if not confirm(
            self,
            title=t("plugins.market.confirm.uninstall_title", "卸载插件"),
            message=t(
                "plugins.market.confirm.uninstall_message",
                "卸载 {name} 会删除插件目录和它的私有数据。确定吗？",
                name=record.name,
            ),
            action_text=t("plugins.market.confirm.uninstall_action", "卸载"),
        ):
            return

        # 宿主是 Qt 对象：先在 UI 线程摘掉视图与记录，后台只删目录。
        host = self._host()
        if host is not None:
            try:
                host.remove(record.plugin_id)
            except Exception:  # noqa: BLE001
                log.exception("remove plugin record before uninstall failed: %s", record.plugin_id)

        def _work() -> str:
            self._marketplace.uninstall(record.plugin_id)
            return record.name

        def _done(name: str) -> None:
            self.ctx.notify(
                t("plugins.market.notify.uninstalled", "已卸载 {name}", name=name), "ok"
            )
            self.refresh()

        def _failed(exc: Exception) -> None:
            self.ctx.notify(
                t("plugins.market.notify.uninstall_failed", "卸载失败：{error}", error=exc), "warn"
            )
            if host is not None:
                try:
                    host.rescan()
                except Exception:  # noqa: BLE001
                    log.exception("plugin rescan after uninstall failure failed")
            self.refresh()

        run_async(_work, on_done=_done, on_error=_failed)

    # ------------------------------------------------------------------
    # 插件源
    # ------------------------------------------------------------------
    def _toggle_sources(self) -> None:
        visible = not self._sources_panel.isVisible()
        self._sources_panel.setVisible(visible)
        if visible:
            self._refresh_sources_ui()

    def _refresh_sources_ui(self) -> None:
        if not hasattr(self, "_sources_layout"):
            return
        _clear_layout(self._sources_layout)
        sources = self._marketplace.sources()
        official = next((item for item in sources if item.official), None)
        self._official_source.setText(
            t(
                "plugins.market.sources.official",
                "官方源（随程序分发）：{path}",
                path=self._marketplace.market_dir / "index.json",
            )
        )
        third = [item for item in sources if not item.official]
        self._sources_empty.setVisible(not third)
        for source in third:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            row_layout.addWidget(Label(source.name, "body"), 1)
            row_layout.addWidget(Label(source.url, "small"), 2)
            remove = IconButton(
                "trash", t("plugins.market.sources.remove_tooltip", "移除这个源"), variant="danger"
            )
            remove.clicked.connect(lambda _checked=False, url=source.url, name=source.name: self._remove_source(url, name))
            row_layout.addWidget(remove)
            self._sources_layout.addWidget(row)
            self._sources_layout.addWidget(Hairline())
        if official is None:  # 理论上不会发生
            self._official_source.setText("")

    def _add_source(self) -> None:
        url = self._source_url.text().strip()
        name = self._source_name.text().strip() or None
        try:
            source = self._marketplace.add_source(url, name)
        except MarketError as exc:
            self.ctx.notify(str(exc), "warn")
            return
        self._source_url.clear()
        self._source_name.clear()
        self._refresh_sources_ui()
        self.ctx.notify(
            t("plugins.market.sources.added", "已添加插件源 {name}", name=source.name), "ok"
        )
        self._load_market(force=True)

    def _remove_source(self, url: str, name: str) -> None:
        if not confirm(
            self,
            title=t("plugins.market.sources.confirm_title", "移除插件源"),
            message=t(
                "plugins.market.sources.confirm_message",
                "移除 {name} 后，市场里来自它的插件条目会消失（已安装的插件不受影响）。",
                name=name,
            ),
            action_text=t("plugins.market.sources.confirm_action", "移除"),
        ):
            return
        self._marketplace.remove_source(url)
        self._refresh_sources_ui()
        self.ctx.notify(
            t("plugins.market.sources.removed", "已移除插件源 {name}", name=name), "info"
        )
        self._load_market(force=True)

    # ------------------------------------------------------------------
    # 已安装页操作
    # ------------------------------------------------------------------
    def _schedule_refresh(self) -> None:
        if self._refresh_pending:
            return
        self._refresh_pending = True
        QTimer.singleShot(0, self._do_scheduled_refresh)

    def _do_scheduled_refresh(self) -> None:
        self._refresh_pending = False
        if self.isVisible():
            self.refresh()

    def _rescan(self) -> None:
        host = self._host()
        if host is None:
            self.ctx.notify(t("plugins.notify.no_host"), "warn")
            return
        host.rescan()
        summary = host.summary()
        self.ctx.notify(
            t("plugins.notify.scan_done", loaded=summary["loaded"], errors=summary["error"]),
            "ok" if summary["error"] == 0 else "warn",
        )
        self._schedule_refresh()

    def _toggle(self, record: PluginRecord) -> None:
        host = self._host()
        if host is None:
            return
        plugin_id = record.plugin_id
        if record.state == STATE_LOADED:
            host.disable(plugin_id)
            self.ctx.notify(t("plugins.notify.disabled", name=record.name), "ok")
        else:
            ok = host.enable(plugin_id)
            current = host.get(plugin_id)
            if ok:
                self.ctx.notify(t("plugins.notify.enabled", name=record.name), "ok")
            else:
                error = current.error if current is not None and current.error else t("plugins.notify.unknown_error")
                self.ctx.notify(t("plugins.notify.enable_failed", error=error), "warn")
        self._schedule_refresh()

    def _reload(self, record: PluginRecord) -> None:
        host = self._host()
        if host is None:
            return
        ok = host.reload(record.plugin_id)
        current = host.get(record.plugin_id)
        if ok:
            self.ctx.notify(t("plugins.notify.reloaded", name=record.name), "ok")
        else:
            error = current.error if current is not None and current.error else t("plugins.notify.unknown_error")
            self.ctx.notify(t("plugins.notify.reload_failed", error=error), "warn")
        self._schedule_refresh()

    def _open_path(self, path) -> None:
        if not _open_local(Path(path)):
            self.ctx.notify(t("plugins.notify.dir_missing", path=path), "warn")

    # ------------------------------------------------------------------
    # 生命周期 / 顶栏
    # ------------------------------------------------------------------
    def on_show(self) -> None:
        self._refresh_pending = False
        self.refresh()
        if self._tabs.index == 1 and not self._market_loaded:
            self._load_market(force=False)

    def on_hide(self) -> None:
        self._refresh_pending = False

    def topbar_fields(self) -> list[str]:
        host = self._host()
        if host is None:
            return [t("plugins.count.host_missing")]
        summary = host.summary()
        return [
            t("plugins.count.loaded", count=summary["loaded"]),
            t("plugins.count.total", count=summary["total"]),
            t("plugins.count.errors", count=summary["error"]),
        ]

    def topbar_actions(self) -> list[QWidget]:
        button = IconButton("refresh", t("plugins.topbar.refresh"))
        button.clicked.connect(self._rescan)
        return [button]
