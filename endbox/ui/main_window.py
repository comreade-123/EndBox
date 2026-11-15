# -*- coding: utf-8 -*-
"""主窗口：无边框 + 圆角毛玻璃，左侧边栏面板 / 顶栏 / 主显示面板。

外壳参考《明日方舟：终末地》官方启动器：整窗圆角、毛玻璃边框，
侧边栏是一块独立的圆角面板，与主显示区之间留出明显间隙。
（内部可点击元素仍然用切角、正文容器直角 —— 只有窗口壳层用圆角。）
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PyQt6 import sip
from PyQt6.QtCore import (
    QAbstractNativeEventFilter,
    QPoint,
    QSize,
    QEasingCurve,
    QEvent,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
)
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

WM_SYSCOMMAND = 0x0112
WM_NCCALCSIZE = 0x0083
SC_MINIMIZE = 0xF020


class _SysCommandFilter(QAbstractNativeEventFilter):
    """接管任务栏按钮的最小化（Windows 发 WM_SYSCOMMAND），让它走渐隐动画。"""

    def __init__(self, window) -> None:
        super().__init__()
        self._window = window

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        if not sys.platform.startswith("win"):
            return False, 0
        try:
            if event_type in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                msg = wintypes.MSG.from_address(int(message))
                # 去掉系统标题栏/边框，但保留 DWM 的圆角与背景材质
                if msg.message == WM_NCCALCSIZE and int(msg.wParam):
                    return True, 0
                if msg.message == WM_SYSCOMMAND:
                    command = int(msg.wParam) & 0xFFF0
        except Exception:  # noqa: BLE001
            pass
        return False, 0


class MainPanel(EWidget):
    """主显示面板：圆角板 + 细线边框，内部含顶栏与主显示区。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._radius = PANEL_RADIUS   # 最大化时置 0，铺满窗口

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = pal()
        glass_on = bool(getattr(self.window(), "_native_glass", False))
        base = QColor(colors.content)
        if glass_on:
            base.setAlpha(192)   # 模糊背景透出来
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        paint.fill_round(p, rect, base, self._radius)
        if self._radius > 0:
            paint.stroke_round(p, rect, colors.rule, self._radius, 1.0)
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
        self._native_glass = False    # 系统实时模糊是否生效
        # 最小化渐隐 / 恢复渐显
        self._fade_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_anim.setDuration(240)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        # 自定义最大化：单一进度动画（几何 + 边距 + 圆角）
        self._maximized = False
        self._normal_geo = None
        self._animating = False
        self._shell_radius = SHELL_RADIUS
        self._shell_anim = QVariantAnimation(self)
        self._shell_anim.setDuration(260)
        self._shell_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._view_opened = False

        self.setWindowTitle(t("shell.window_title", "EndBox · 终末地工具箱"))
        self._taskbar_filter = _SysCommandFilter(self)
        app = QApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self._taskbar_filter)
        # 保留系统窗口样式（DWM 圆角 + 背景模糊），标题栏在原生事件里裁掉
        self.setWindowFlags(Qt.WindowType.Window)
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
        self.topbar.min_btn.clicked.connect(self._minimize_with_fade)
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
        self._enable_glass()
        # 从最小化唤回时透明度可能是 0，这里兜底一次，保证窗口可见
        if not self.isMinimized() and float(self.windowOpacity()) < 0.99:
            self._fade_window(1.0)

    def _apply_mask(self) -> None:
        # 保留系统窗口样式后由 DWM 画圆角，自绘遮罩会盖掉系统模糊
        self.clearMask()

    def _enable_glass(self) -> None:
        """开启系统实时模糊（DWM 实时合成桌面，桌面动它跟着动）。"""
        if not config.GLASS_ENABLED or self._maximized:
            self._native_glass = False
            return
        colors = pal()
        tint = QColor(colors.window).name()
        alpha = 0x38 if colors.is_dark else 0x58
        ok = glass.apply_acrylic(self, tint=tint, alpha=alpha)
        self._native_glass = ok
        log.info("realtime glass: %s", self._native_glass)
        self.update()

    def _disable_glass(self) -> None:
        if self._native_glass:
            glass.disable_realtime_glass(self)
        self._native_glass = False

    def _set_shell_mode(self, maximized: bool) -> None:
        """最大化：去掉外边距、面板圆角与毛玻璃，铺满屏幕；还原时恢复。"""
        gap = 0 if maximized else SHELL_GAP
        self._outer.setContentsMargins(gap, gap, gap, gap)
        radius = 0 if maximized else PANEL_RADIUS
        self.main_panel._radius = radius  # noqa: SLF001
        self.sidebar._radius = 0 if maximized else 12  # noqa: SLF001
        self._shell_radius = 0 if maximized else SHELL_RADIUS
        if maximized:
            self._disable_glass()
        self.main_panel.update()
        self.sidebar.update()
        self.update()

    def _apply_shell_interim(self, t: float) -> None:
        """动画中间态：外边距 / 面板圆角 / 窗口圆角一起插值，避免跳变。"""
        t = max(0.0, min(1.0, float(t)))
        gap = SHELL_GAP * (1.0 - t)
        self._outer.setContentsMargins(
            int(round(gap)), int(round(gap)), int(round(gap)), int(round(gap))
        )
        self.main_panel._radius = int(round(PANEL_RADIUS * (1.0 - t)))  # noqa: SLF001
        self.sidebar._radius = int(round(12 * (1.0 - t)))  # noqa: SLF001
        self._shell_radius = int(round(SHELL_RADIUS * (1.0 - t)))
        self.main_panel.update()
        self.sidebar.update()
        self.update()

    def _animate_shell(self, target, t_from: float, t_to: float, on_done=None) -> None:
        """单一进度动画：窗口几何 + 外边距 + 圆角同步过渡（最大化 / 还原共用）。"""
        if not self.ctx.settings.get("app.animations", True):
            self.setGeometry(target)
            if on_done is not None:
                on_done()
            return
        start = self.geometry()
        self._animating = True
        self._apply_mask()          # 动画期间用矩形窗口，避免每帧重算圆角遮罩
        self._shell_anim.stop()
        try:
            self._shell_anim.finished.disconnect()
            self._shell_anim.valueChanged.disconnect()
        except TypeError:
            pass

        def _step(value) -> None:
            t = float(value)
            progress = max(0.0, min(1.0, (t - t_from) / (t_to - t_from) if t_to != t_from else 1.0))
            self.setGeometry(
                int(round(start.x() + (target.x() - start.x()) * progress)),
                int(round(start.y() + (target.y() - start.y()) * progress)),
                int(round(start.width() + (target.width() - start.width()) * progress)),
                int(round(start.height() + (target.height() - start.height()) * progress)),
            )
            self._apply_shell_interim(t)

        def _finished() -> None:
            self._animating = False
            if on_done is not None:
                on_done()

        self._shell_anim.valueChanged.connect(_step)
        self._shell_anim.finished.connect(_finished)
        self._shell_anim.setStartValue(float(t_from))
        self._shell_anim.setEndValue(float(t_to))
        self._shell_anim.start()

    def toggle_maximize(self) -> None:
        """自定义最大化：几何/边距/圆角一起平滑过渡；最大化态无毛玻璃、无圆角。"""
        if self._animating:
            return
        if self._maximized:
            self._maximized = False
            self.topbar.max_btn.set_icon_name("max")
            target = self._normal_geo if self._normal_geo is not None else self.geometry()

            def _done() -> None:
                self._set_shell_mode(False)
                self._apply_mask()
                self._enable_glass()
                self.update()

            self._animate_shell(target, 1.0, 0.0, _done)
            return

        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        self._normal_geo = self.geometry()
        self._maximized = True
        self.topbar.max_btn.set_icon_name("restore")
        self._set_shell_mode(True)

        def _done() -> None:
            self._apply_mask()
            self.update()

        self._animate_shell(screen.availableGeometry(), 0.0, 1.0, _done)

    def toggle_theme(self) -> None:
        mode = theme.toggle()
        self.ctx.settings.set("app.theme", mode)
        self.ctx.notify(t("shell.theme_to_light" if mode == "light" else "shell.theme_to_dark"), "ok")

    def _on_theme_changed(self, mode: str) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(app_qss(pal()))
        restyle(self)
        self._enable_glass()

    # -- 最小化渐隐 / 恢复渐显 -----------------------------------------------
    def _fade_window(self, target: float, on_done=None) -> None:
        if not self.ctx.settings.get("app.animations", True):
            self.setWindowOpacity(target)
            if on_done is not None:
                on_done()
            return
        self._fade_anim.stop()
        try:
            self._fade_anim.finished.disconnect()
        except TypeError:
            pass
        self._fade_anim.setStartValue(float(self.windowOpacity()))
        self._fade_anim.setEndValue(float(target))
        if on_done is not None:
            self._fade_anim.finished.connect(on_done)
        self._fade_anim.start()

    def _minimize_with_fade(self) -> None:
        def _done() -> None:
            if not self.isMinimized():
                self.showMinimized()

        self._fade_window(0.0, _done)

    def minimize_from_taskbar(self) -> None:
        """任务栏图标点击（Windows 的 WM_SYSCOMMAND）→ 渐隐最小化。"""
        if self.isMinimized():
            return
        self._minimize_with_fade()

    def ensure_minimized(self) -> None:
        """兜底：渐隐回调万一没生效，直接进最小化，保证任务栏按钮一定有反应。"""
        if not self.isMinimized():
            self.setWindowOpacity(1.0)
            self.showMinimized()

    def restore_for_drag(self, global_pos) -> None:
        """最大化时拖顶栏：先还原到原尺寸，并让光标仍落在顶栏上。"""
        if not self._maximized:
            return
        self._shell_anim.stop()
        self._animating = False
        target = QRect(self._normal_geo) if self._normal_geo is not None else QRect(self.geometry())
        screen = self.screen() or QApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else target
        target.setWidth(min(target.width(), area.width()))
        target.setHeight(min(target.height(), area.height()))
        self._maximized = False
        self.topbar.max_btn.set_icon_name("max")
        self.setGeometry(target)
        self._set_shell_mode(False)
        self._apply_mask()
        self._enable_glass()
        ratio = (global_pos.x() - area.x()) / max(1, area.width())
        ratio = max(0.05, min(0.95, ratio))
        self.move(int(global_pos.x() - target.width() * ratio), int(global_pos.y() - 18))
        self.update()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        event_type = event.type()
        if event_type == QEvent.Type.WindowActivate and self.isVisible():
            QTimer.singleShot(120, self._refresh_glass)
        if event_type == QEvent.Type.WindowStateChange:
            if self.isMinimized():
                return
            # 从最小化恢复：从透明渐显，避免"啪"地出现
            if float(self.windowOpacity()) < 0.99:
                self._fade_window(1.0)
            self._set_shell_mode(self._maximized)
        elif event_type in (QEvent.Type.WindowActivate, QEvent.Type.Show):
            # 任务栏 / 托盘唤回时保证一定看得到（最小化渐隐后透明度是 0）
            if not self.isMinimized() and float(self.windowOpacity()) < 0.99:
                self._fade_window(1.0)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = pal()
        if self._maximized and not self._animating:
            # 最大化：无毛玻璃、无圆角，整块铺满
            p.fillRect(self.rect(), colors.window)
            p.end()
            return
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        radius = self._shell_radius
        if self._native_glass and not self._animating:
            tint = QColor(colors.paper)
            tint.setAlpha(30 if colors.is_dark else 56)
            p.fillPath(paint.round_rect(rect, radius), tint)
        else:
            paint.fill_round(p, rect, colors.window, radius)
        if radius > 0:
            paint.stroke_round(p, rect, colors.rule, radius, 1.0)
        p.end()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not self._animating:
            self._apply_mask()
        if self.toast is not None:
            self.toast.setGeometry(0, 0, self.width(), self.height())
            self.toast.raise_()

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)

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
        if not self._maximized:
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
        if event.button() == Qt.MouseButton.LeftButton and not self._maximized:
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
        if self._maximized and self._normal_geo is not None:
            geo = self._normal_geo
        else:
            geo = self.geometry()
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
