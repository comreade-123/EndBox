# -*- coding: utf-8 -*-
"""主窗口：无边框 + 圆角毛玻璃，左侧边栏面板 / 顶栏 / 主显示面板。

外壳参考《明日方舟：终末地》官方启动器：整窗圆角、毛玻璃边框，
侧边栏是一块独立的圆角面板，与主显示区之间留出明显间隙。
（内部可点击元素仍然用切角、正文容器直角 —— 只有窗口壳层用圆角。）
"""
from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from endbox import config
from endbox.app_context import AppContext
from endbox.core.event_bus import Topics, bus
from endbox.core.log import get_logger
from endbox.core.text import t
from endbox.theme import paint
from endbox.theme.style import app_qss
from endbox.theme.tokens import theme
from endbox.ui import glass
from endbox.ui.kit import ToastHost
from endbox.ui.kit.base import EWidget, pal, restyle
from endbox.ui.sidebar import Sidebar
from endbox.ui.topbar import TopBar
from endbox.ui.views.base import BaseView, ViewRegistry

log = get_logger("ui.window")

EDGE = 8            # 缩放热区
SHELL_GAP = 10      # 窗口边距 / 侧边栏与主面板之间的间隙
SHELL_RADIUS = 16   # 整窗圆角
PANEL_RADIUS = 12   # 侧边栏、主显示面板圆角


class MainPanel(EWidget):
    """主显示面板：圆角板 + 细线边框，内部含顶栏与主显示区。"""

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = pal()
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        paint.fill_round(p, rect, colors.content, PANEL_RADIUS)
        paint.stroke_round(p, rect, colors.rule, PANEL_RADIUS, 1.0)
        p.end()


class ContentArea(EWidget):
    """主显示面板里的内容区（透明，底板由 MainPanel 画）。"""


class MainWindow(QWidget):
    def __init__(self, ctx: AppContext, registry: ViewRegistry):
        super().__init__()
        self.ctx = ctx
        self.registry = registry
        self.ctx.main_window = self
        self.ctx.open_view = self.show_view
        self._glass_ok = False
        self._glass_base = None      # 当前帧：窗口位置对应的毛玻璃图
        self._glass_field = None     # 整屏模糊场（只抓一次，移动时按偏移裁）
        self._glass_geo = None       # 底图对应的窗口几何
        # 移动/缩放时合并重绘，最多每 16ms 取一帧，避免拖动时反复裁图
        self._glass_timer = QTimer(self)
        self._glass_timer.setSingleShot(True)
        self._glass_timer.setInterval(16)
        self._glass_timer.timeout.connect(self._sync_glass_frame)
        self._view_opened = False

        self.setWindowTitle(t("shell.window_title", "EndBox · 终末地工具箱"))
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(1040, 700)
        w = int(ctx.settings.get("window.w", 1320) or 1320)
        h = int(ctx.settings.get("window.h", 860) or 860)
        self.resize(w, h)
        x = ctx.settings.get("window.x")
        y = ctx.settings.get("window.y")
        self._placed = False
        if isinstance(x, int) and isinstance(y, int):
            self.move(x, y)
            self._placed = True

        self._outer = QHBoxLayout(self)
        self._outer.setContentsMargins(SHELL_GAP, SHELL_GAP, SHELL_GAP, SHELL_GAP)
        self._outer.setSpacing(SHELL_GAP)

        self.sidebar = Sidebar(ctx, registry, self)
        self._outer.addWidget(self.sidebar)

        self.main_panel = MainPanel(self)
        self._outer.addWidget(self.main_panel, 1)
        panel_layout = QVBoxLayout(self.main_panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)

        self.topbar = TopBar(ctx, self.main_panel)
        panel_layout.addWidget(self.topbar)

        self.content = ContentArea(self.main_panel)
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(18, 14, 18, 18)
        content_layout.setSpacing(12)
        panel_layout.addWidget(self.content, 1)

        self.stack = QStackedWidget(self.content)
        self.stack.setStyleSheet("background: transparent;")
        content_layout.addWidget(self.stack, 1)

        self.toast = ToastHost(self)
        self.toast.show()
        self.toast.raise_()

        # 信号
        self.sidebar.view_selected.connect(self.show_view)
        self.topbar.settings_clicked.connect(lambda: self.show_view("settings"))
        self.topbar.theme_toggled.connect(self.toggle_theme)
        self.topbar.min_btn.clicked.connect(self.showMinimized)
        self.topbar.max_btn.clicked.connect(self.toggle_maximize)
        self.topbar.close_btn.clicked.connect(self.close)

        theme.changed.connect(self._on_theme_changed)
        self.setMouseTracking(True)
        self._install_shortcuts()

        # 用户要求：每次打开都在首页（问候 + 模块入口），不恢复上次的模块
        default_view = "home" if self.registry.has("home") else ""
        if not default_view:
            classes = self.registry.classes()
            default_view = classes[0].key if classes else ""
        # 先抓窗口背后桌面，作为毛玻璃底图（show 之前抓，避免抓到自己）
        if config.GLASS_ENABLED:
            self._capture_glass()
        if default_view:
            QTimer.singleShot(0, lambda: self._open_default(default_view))

    def _install_shortcuts(self) -> None:
        """Ctrl+1…9 切模块，Ctrl+Shift+T 换主题，Ctrl+Q 退出。"""
        from PyQt6.QtGui import QKeySequence, QShortcut

        for index in range(1, 10):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index}"), self)
            shortcut.activated.connect(lambda i=index: self._shortcut_view(i))
        QShortcut(QKeySequence("Ctrl+Shift+T"), self).activated.connect(self.toggle_theme)
        QShortcut(QKeySequence("Ctrl+Q"), self).activated.connect(self.close)

    def _shortcut_view(self, index: int) -> None:
        classes = self.registry.classes()
        if 0 < index <= len(classes):
            self.show_view(classes[index - 1].key)


    # -- 视图 ---------------------------------------------------------------
    def _open_default(self, key: str) -> None:
        if sip.isdeleted(self):
            return
        if not self._view_opened:
            self.show_view(key)

    def show_view(self, key: str) -> None:
        if not self.registry.has(key):
            log.warning("view %s not registered", key)
            return
        view = self.registry.instance(key, self.ctx)
        if view is None:
            return
        first_show = not self._view_opened
        self._view_opened = True
        if self.stack.indexOf(view) < 0:
            self.stack.addWidget(view)
        already_current = self.stack.currentWidget() is view
        if already_current and not first_show and getattr(self, "_current_key", "") == key:
            # 点了当前模块的按钮：什么都不用重建
            return
        if not already_current:
            old = self.stack.currentWidget()
            if isinstance(old, BaseView):
                try:
                    old.on_hide()
                except Exception:  # noqa: BLE001
                    log.exception("view %s on_hide failed", old.key)
            self.stack.setCurrentWidget(view)
        self._current_key = key
        self.sidebar.set_current(key)
        self.topbar.set_view(view.eyebrow, view.title)
        self.topbar.set_icon(view.icon)
        try:
            view.refresh()
        except Exception:  # noqa: BLE001
            log.exception("view %s refresh failed", key)
        try:
            self.topbar.set_fields(view.topbar_fields())
            self.topbar.set_actions(view.topbar_actions())
        except Exception:  # noqa: BLE001
            log.exception("view %s topbar failed", key)
        try:
            view.on_show()
        except Exception:  # noqa: BLE001
            log.exception("view %s on_show failed", key)
        self.ctx.settings.set("app.last_view", key, save=False)
        if key != "home" and self.ctx.settings.get("app.first_run", True):
            self.ctx.settings.set("app.first_run", False, save=False)
        if first_show or not already_current:
            self._animate_in(view)
        bus.emit(Topics.VIEW_CHANGED, key=key)

    def _animate_in(self, view: QWidget) -> None:
        if not self.ctx.settings.get("app.animations", True):
            return
        # 离屏/测试环境不做透明度动画：QGraphicsOpacityEffect 在无窗口的
        # 对象销毁顺序下容易留下悬空回调（曾导致段错误），真实窗口才有必要。
        if QApplication.platformName() == "offscreen":
            return
        effect = QGraphicsOpacityEffect(view)
        view.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", view)
        anim.setDuration(320)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _clear() -> None:
            # 窗口/视图可能在动画结束前被销毁（测试、快速退出），必须守卫
            if sip.isdeleted(view):
                return
            try:
                view.setGraphicsEffect(None)
            except RuntimeError:
                pass

        anim.finished.connect(_clear)
        anim.start()
        view._entrance_anim = anim  # 防止被回收

    @staticmethod
    def _alive(widget) -> bool:
        return widget is not None and not sip.isdeleted(widget)

    def refresh_current(self) -> None:
        view = self.stack.currentWidget()
        if isinstance(view, BaseView):
            view.refresh()
            try:
                self.topbar.set_fields(view.topbar_fields())
            except Exception:  # noqa: BLE001
                pass

    # -- 窗口 ---------------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._placed:
            screen = self.screen() or QApplication.primaryScreen()
            if screen is not None:
                geo = screen.availableGeometry()
                self.move(geo.center().x() - self.width() // 2, geo.center().y() - self.height() // 2)
            self._placed = True
        self._apply_mask()

    def _apply_mask(self) -> None:
        if self.isMaximized():
            self.clearMask()
        else:
            self.setMask(paint.window_mask(self.width(), self.height(), SHELL_RADIUS))

    def _capture_glass(self, *, hide_window: bool = False) -> None:
        """抓一次整屏，做成低分辨率模糊场。

        移动/缩放**不再重新截屏**（截屏会让窗口闪一下）：只从这张模糊场里按窗口位置
        裁一块放大，所以背景会跟着窗口真实移动，每帧开销约 1–2ms。
        换壁纸或桌面内容变了，由设置页「重新抓一次」显式重抓。
        """
        if not config.GLASS_ENABLED:
            return
        geo = self.frameGeometry()
        if geo.width() < 64 or geo.height() < 64:
            return
        if hide_window:
            self.setWindowOpacity(0.0)
            QApplication.processEvents()
        try:
            screens = QApplication.screens()
            field = glass.GlassField.capture(screens)
            if field is None:
                self._glass_ok = False
                self._glass_field = None
                return
            self._glass_field = field
            self._glass_geo = geo
            self._glass_ok = True
            self._sync_glass_frame()
        finally:
            if hide_window:
                self.setWindowOpacity(1.0)
        self.update()

    def _schedule_glass(self) -> None:
        """窗口移动/缩放后请求取一帧（合并到 16ms 内一次）。"""
        if not config.GLASS_ENABLED or self._glass_field is None:
            return
        if not self.ctx.settings.get("app.glass_follow", True):
            return
        self._glass_timer.start()

    def _sync_glass_frame(self) -> None:
        """从模糊场裁出当前窗口位置的一帧。"""
        if not config.GLASS_ENABLED or self._glass_field is None:
            return
        geo = self.frameGeometry()
        if geo.width() < 64 or geo.height() < 64:
            return
        frame = self._glass_field.frame(geo, self.size())
        if frame is None:
            return
        self._glass_base = frame
        self._glass_geo = geo
        self._glass_ok = True
        self.update()

    def refresh_glass(self) -> None:
        """设置页调用：重新截取当前桌面作为毛玻璃底图（会短暂透明，用户主动触发）。"""
        self._capture_glass(hide_window=True)

    # -- 窗口移动 / 缩放：实时跟随 -------------------------------------------
    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        self._schedule_glass()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._schedule_glass()

    def toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
            self.topbar.max_btn.set_icon_name("max")
        else:
            self.showMaximized()
            self.topbar.max_btn.set_icon_name("restore")
        if self.isMaximized():
            self.showMaximized()
        self._apply_mask()
        self._schedule_glass()
        self.update()

    def toggle_theme(self) -> None:
        mode = theme.toggle()
        self.ctx.settings.set("app.theme", mode)
        self.ctx.notify(t("shell.theme_to_light" if mode == "light" else "shell.theme_to_dark"), "ok")

    def _on_theme_changed(self, mode: str) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(app_qss(pal()))
        restyle(self)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = pal()
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        if self._glass_base is not None:
            # 模糊桌面 + 纸色染色 = 毛玻璃（最大化时也用整屏底图）
            p.setClipPath(paint.round_rect(rect, SHELL_RADIUS))
            p.drawPixmap(self.rect(), self._glass_base)
            tint = QColor(colors.paper)
            tint.setAlpha(150 if colors.is_dark else 176)
            p.fillRect(self.rect(), tint)
        else:
            paint.fill_round(p, rect, colors.window, SHELL_RADIUS)
        paint.stroke_round(p, rect, colors.rule, SHELL_RADIUS, 1.0)
        p.end()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_mask()
        if self.toast is not None:
            self.toast.setGeometry(0, 0, self.width(), self.height())
            self.toast.raise_()

    # -- 无边框窗口：边缘缩放 ------------------------------------------------
    def _edges_at(self, pos) -> Qt.Edge:
        edges = Qt.Edge(0)
        if pos.x() <= EDGE:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= self.width() - EDGE:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= EDGE:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= self.height() - EDGE:
            edges |= Qt.Edge.BottomEdge
        return edges

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self.isMaximized():
            edges = self._edges_at(event.position().toPoint())
            if edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge) and edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
                self.setCursor(Qt.CursorShape.SizeVerCursor)
            else:
                self.unsetCursor()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and not self.isMaximized():
            edges = self._edges_at(event.position().toPoint())
            if edges:
                handle = self.windowHandle()
                if handle is not None:
                    handle.startSystemResize(edges)
                    return
        super().mousePressEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.unsetCursor()
        super().leaveEvent(event)

    # -- 持久化 -------------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802
        geo = self.normalGeometry() if self.isMaximized() else self.geometry()
        self.ctx.settings.update(
            {
                "window.w": geo.width(),
                "window.h": geo.height(),
                "window.x": geo.x(),
                "window.y": geo.y(),
            }
        )
        bus.emit("app.closing")
        super().closeEvent(event)
