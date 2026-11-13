# -*- coding: utf-8 -*-
"""整机集成测试：真实服务 + 插件 + 主窗口，在离屏 Qt 下跑一遍主流程。

    python run_tests.py tests/test_integration.py -q
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def app_window(qapp, tmp_settings):
    """装配一套完整应用（不经过 main.py 的单实例锁）。"""
    from endbox.app import _init_services, _load_plugins
    from endbox.app_context import AppContext
    from endbox.core.event_bus import EventBus
    from endbox.services import Services
    from endbox.ui.main_window import MainWindow
    from endbox.ui.views import load_views
    from endbox.ui.views.base import ViewRegistry

    ctx = AppContext(settings=tmp_settings, bus=EventBus(), services=Services())
    registry = ViewRegistry()
    errors = load_views(registry)
    # 所有内置视图必须都能导入（缺失说明模块被改坏）
    assert errors == {}, errors
    _init_services(ctx)
    window = MainWindow(ctx, registry)
    ctx.toast = window.toast.show_message
    _load_plugins(ctx, registry)
    yield ctx, registry, window
    try:
        window.close()
        qapp.processEvents()
    except Exception:  # noqa: BLE001
        pass
    for service in (ctx.services.hud, ctx.services.timeline, ctx.services.account):
        stop = getattr(service, "stop", None)
        if callable(stop):
            stop()
    if ctx.services.plugins is not None:
        ctx.services.plugins.unload_all()


def test_no_dangling_callbacks_after_close(qapp):
    """回归：窗口关闭后仍有排队的动画/定时器回调不能崩（以前的段错误）。"""
    from PyQt6.QtTest import QTest

    QTest.qWait(100)
    qapp.processEvents()
    QTest.qWait(50)
    qapp.processEvents()


def test_app_boots_into_home(app_window):
    from PyQt6.QtTest import QTest

    ctx, registry, window = app_window
    QTest.qWait(120)
    assert window.stack.currentWidget() is not None
    assert window.stack.currentWidget().key == "home"
    # 侧边栏高亮必须和当前视图一致
    assert window.sidebar._current == "home"


def test_all_services_and_plugins_ready(app_window):
    ctx, registry, window = app_window
    snapshot = ctx.services.snapshot()
    assert snapshot == {"account": True, "timeline": True, "hud": True, "plugins": True}
    plugins = ctx.services.plugins
    assert len(plugins.loaded) >= 3
    assert ctx.settings.get("app.theme") in ("dark", "light")


def test_every_view_opens_and_theme_toggles(app_window):
    from endbox.theme.tokens import theme

    ctx, registry, window = app_window
    keys = [cls.key for cls in registry.classes()]
    for key in keys:
        window.show_view(key)
        current = window.stack.currentWidget()
        assert current is not None and current.key == key, key
        # 每个视图的顶栏字段不能抛异常
        window.topbar.set_fields(current.topbar_fields())
    mode = theme.mode
    window.toggle_theme()
    assert theme.mode != mode
    window.toggle_theme()
    assert theme.mode == mode
    window.show_view("home")


def test_timeline_service_clock_and_snapshot(app_window):
    from PyQt6.QtTest import QTest

    ctx, registry, window = app_window
    timeline = ctx.services.timeline
    state = timeline.snapshot()
    # 向后兼容字段（home / HUD / 插件都依赖）
    for key in ("t", "playing", "duration", "team", "active", "events", "buffs"):
        assert key in state, key
    timeline.play()
    QTest.qWait(300)
    assert timeline.snapshot()["playing"] is True
    timeline.pause()
    timeline.seek(0.0)
    assert timeline.snapshot()["playing"] is False


def test_hud_toggle_is_safe(app_window):
    ctx, registry, window = app_window
    hud = ctx.services.hud
    start = bool(getattr(hud, "enabled", False))
    hud.set_enabled(True)
    assert hud.enabled is True
    hud.set_enabled(True)  # 幂等
    hud.set_enabled(False)
    assert hud.enabled is False
    if start:
        hud.set_enabled(True)


def test_plugin_views_registered_with_marker(app_window):
    ctx, registry, window = app_window
    plugin_views = registry.classes("plugin")
    assert len(plugin_views) >= 3
    for view_cls in plugin_views:
        assert view_cls.key.startswith("plugin_")
        window.show_view(view_cls.key)
        assert window.stack.currentWidget().key == view_cls.key
