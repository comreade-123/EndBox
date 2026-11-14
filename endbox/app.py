# -*- coding: utf-8 -*-
"""EndBox 启动入口：应用装配、单实例、主题、服务与插件宿主。"""
from __future__ import annotations

import ctypes
import sys
import traceback

from PyQt6.QtCore import QLockFile, Qt
from PyQt6.QtWidgets import QApplication

from endbox import __version__, config
from endbox.app_context import AppContext
from endbox.core.event_bus import bus
from endbox.core.log import get_logger, setup as setup_logging
from endbox.core.net import close as close_net
from endbox.core.text import t
from endbox.services import Services
from endbox.theme import fonts
from endbox.theme.style import app_qss
from endbox.theme.tokens import theme
from endbox.ui.branding import app_icon

log = get_logger("app")


def _set_app_user_model_id() -> None:
    if sys.platform.startswith("win"):
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("EndBox.Toolbox.0.1")
        except Exception:  # noqa: BLE001
            pass


class SingleInstance:
    def __init__(self) -> None:
        config.ensure_dirs()
        self._lock = QLockFile(str(config.DATA_DIR / "endbox.lock"))
        # 默认 30s 的陈旧锁检测：进程被强杀后下次仍能正常启动
        self._lock.setStaleLockTime(30_000)
        self.ok = self._lock.tryLock(100)
        if not self.ok:
            self._lock.removeStaleLockFile()
            self.ok = self._lock.tryLock(100)

    def release(self) -> None:
        try:
            self._lock.unlock()
        except Exception:  # noqa: BLE001
            pass


def build_context() -> AppContext:
    return AppContext(
        settings=config.settings,
        bus=bus,
        services=Services(),
    )


def _init_services(ctx: AppContext) -> None:
    """按模块尝试实例化服务；失败只记录，不影响主程序。"""
    factories = (
        ("account", "endbox.modules.account"),
        ("timeline", "endbox.modules.timeline"),
        ("hud", "endbox.modules.hud"),
        ("update", "endbox.modules.update"),
    )
    for name, module_name in factories:
        try:
            module = __import__(module_name, fromlist=["create_service"])
            factory = getattr(module, "create_service", None)
            if factory is None:
                log.info("%s: create_service not found yet", module_name)
                continue
            service = factory(ctx)
            setattr(ctx.services, name, service)
            log.info("%s service ready", name)
        except Exception:  # noqa: BLE001
            log.exception("%s service init failed", name)


def _load_plugins(ctx: AppContext, registry) -> None:
    try:
        from endbox.plugins.host import PluginHost

        host = PluginHost(ctx, registry)
        ctx.services.plugins = host
        host.load_all()
    except Exception:  # noqa: BLE001
        log.exception("plugin host init failed")


def main(argv: list[str] | None = None) -> int:
    config.ensure_dirs()
    setup_logging()
    config.settings.set("app.version", __version__, save=False)
    log.info("EndBox %s starting", __version__)

    instance = SingleInstance()
    if not instance.ok:
        # 已有实例在运行：提示后退出（后续可加唤醒信号）
        log.warning("another EndBox instance is running")
        from PyQt6.QtWidgets import QMessageBox

        app = QApplication(argv or sys.argv)
        QMessageBox.information(None, t("app.name", "EndBox"), t("app.single_instance", "EndBox 已经在运行了。"))
        return 0

    _set_app_user_model_id()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("EndBox")
    app.setOrganizationName("EndBox")
    app.setApplicationDisplayName(t("app.title", "EndBox · 终末地工具箱"))
    app.setWindowIcon(app_icon())
    fonts.load_fonts()

    mode = str(config.settings.get("app.theme", "dark"))
    theme.set_mode(mode, emit=False)
    app.setStyleSheet(app_qss(theme.p))

    def _excepthook(exc_type, exc_value, exc_tb):
        log.error("unhandled exception: %s", "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook

    ctx = build_context()
    _init_services(ctx)

    from endbox.ui.main_window import MainWindow
    from endbox.ui.views import load_views
    from endbox.ui.views.base import ViewRegistry

    registry = ViewRegistry()
    errors = load_views(registry)
    if errors:
        log.info("views not loaded: %s", errors)

    window = MainWindow(ctx, registry)
    ctx.toast = window.toast.show_message
    _load_plugins(ctx, registry)
    window.show()

    code = app.exec()

    # 退出清理
    try:
        for service in (ctx.services.hud, ctx.services.timeline, ctx.services.account):
            stop = getattr(service, "stop", None)
            if callable(stop):
                stop()
        host = ctx.services.plugins
        if host is not None:
            host.unload_all()
    except Exception:  # noqa: BLE001
        log.exception("cleanup failed")
    close_net()
    config.settings.save()
    instance.release()
    log.info("EndBox exited with code %s", code)
    return code
