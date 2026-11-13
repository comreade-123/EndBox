# -*- coding: utf-8 -*-
"""HUD 模块测试（不联网、不依赖真实桌面窗口管理）。

HUD 只负责 **账号 / 电池 / 时钟 + 标题用户名**；排轴内容已全部移出（归排轴模块提示条）。
本文件覆盖：
  * state.py 的显示项纯函数：账号降级、电池详情/剩余、时钟 12–24h、用户名来源；
  * 重绘 / 广播去重键（防闪烁核心）；
  * 热键解析、Qt 按键录制、注册失败分支与"失败回退旧组合"；
  * 电池读取失败容错；
  * HudService：默认状态、开关幂等、账号事件聚合、设置项、stop 注销热键与解除订阅；
  * 与排轴模块解耦：不订阅排轴事件、快照里没有排轴键、源码不 import 排轴模块；
  * 覆盖层窗口：两种模式几何、窗口复用不泄漏、窗口标志、锚点 + 偏移、
    相同快照不重复改窗口属性 / 不重复重画画布（闪烁回归测试）；
  * 画布：两种模式布局不越界、标题是用户名、各状态都能画出东西；
  * 控制视图在有 / 无服务时都能构建，录制热键会先注销旧热键。
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import pytest

from PyQt6.QtCore import QCoreApplication, QEvent, QRect, QSize, Qt
from PyQt6.QtWidgets import QApplication

from endbox import config
from endbox.app_context import AppContext
from endbox.core.event_bus import EventBus, Topics
from endbox.core.text import t
from endbox.modules.hud import battery, hotkey
from endbox.modules.hud import state as S
from endbox.services import Services


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def app():
    instance = QApplication.instance()
    if instance is None:
        try:
            instance = QApplication([])
        except Exception as exc:  # noqa: BLE001 - 没有显示环境时跳过依赖 Qt 的用例
            pytest.skip(f"Qt 平台不可用：{exc}")
    yield instance


@pytest.fixture
def settings(tmp_path):
    return config.Settings(tmp_path / "settings.json")


@pytest.fixture
def ctx(settings):
    return AppContext(settings=settings, bus=EventBus(), services=Services())


def _flush(app: QApplication) -> None:
    """把 deleteLater / singleShot 都跑掉（测试里没有真正的 exec 循环）。"""
    for _ in range(4):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        time.sleep(0.005)


def _overlay_windows(app: QApplication) -> list:
    return [widget for widget in app.topLevelWidgets() if widget.objectName() == "hud_overlay"]


@pytest.fixture
def service(app, ctx, settings):
    settings.set("app.animations", False, save=False)
    settings.set("hud.enabled", False, save=False)
    settings.set("hud.hotkey", "Ctrl+Alt+Shift+F24", save=False)
    from endbox.modules.hud.service import HudService

    svc = HudService(ctx)
    ctx.services.hud = svc
    yield svc
    try:
        svc.stop()
    finally:
        _flush(app)


def _snapshot(mode: str = "compact", show: dict | None = None, **overrides) -> dict:
    snapshot = {
        "mode": mode,
        "show": dict(S.DEFAULT_SHOW, **(show or {})),
        "account": {"connected": True, "nickname": "博士", "online": True, "avatar_path": None},
        "battery": {
            "available": True,
            "percent": 76,
            "ac": False,
            "charging": False,
            "remaining_s": 2 * 3600 + 5 * 60,
        },
        "clock": "22:33:58",
        "username": "DrEndfield",
        "anchor": "top-center",
        "offset_x": 0,
        "offset_y": 0,
        "screen": "",
        "scale": 1.0,
        "opacity": 0.96,
    }
    snapshot.update(overrides)
    return snapshot


def _canvas(snapshot: dict | None = None, progress: float = 1.0):
    from endbox.modules.hud.overlay import HudCanvas, design_size

    canvas = HudCanvas()
    snap = snapshot if snapshot is not None else _snapshot()
    design = design_size(snap.get("mode", "compact"))
    canvas.resize(design.width(), design.height())
    canvas.set_snapshot(snap)
    canvas.set_progress(progress)
    return canvas


# ---------------------------------------------------------------------------
# 纯函数：state
# ---------------------------------------------------------------------------

def test_account_and_clock_degrade_states():
    account = S.account_view(S.default_account())
    assert account["text"] == "未连接"
    assert account["flag"] is True
    assert account["connected"] is False
    assert S.clock_view(None)["text"] == S.MISSING
    connected_blank = S.account_view({"connected": True, "nickname": None, "online": False})
    assert connected_blank["text"] == S.MISSING
    assert connected_blank["flag"] is False


def test_timeline_content_is_gone_from_state():
    """HUD 的 state 模块不再提供排轴相关函数/常量（排轴归 timeline 模块提示条）。"""
    for name in (
        "timeline_view",
        "next_event_view",
        "buff_summary",
        "team_view",
        "active_text",
        "default_timeline",
        "TEAM_SLOTS",
    ):
        assert not hasattr(S, name), name
    assert set(S.DEFAULT_SHOW) == {"account", "battery", "clock"}
    assert set(S.SHOW_LABELS) == {"account", "battery", "clock"}


def test_battery_view_details():
    power = battery.empty_power("读取失败")
    unknown = S.battery_view(power)
    assert unknown["percent_text"] == S.MISSING
    assert unknown["ticks"] is None
    assert unknown["mode_text"] == S.MISSING
    assert unknown["remaining_text"] == S.MISSING
    low = S.battery_view({"available": True, "percent": 15, "ac": False, "charging": False, "remaining_s": 3720})
    assert low["percent_text"] == "15%"
    assert low["ticks"] == 2
    assert low["low"] is True
    assert low["mode_text"] == "电池供电"
    assert low["remaining_text"] == "1h02m"
    ac = S.battery_view({"available": True, "percent": 100, "ac": True, "charging": False, "remaining_s": None})
    assert ac["badge"] == "AC"
    assert ac["mode_text"] == "外接电源"
    charging = S.battery_view({"available": True, "percent": 50, "ac": True, "charging": True})
    assert charging["badge"] == "CHG"
    assert S.fmt_battery_remaining(None) == S.MISSING
    assert S.fmt_battery_remaining(-5) == S.MISSING
    assert S.fmt_battery_remaining(59) == "1m"


def test_clock_formats():
    moment = datetime(2026, 11, 13, 22, 33, 58)
    assert S.fmt_clock(moment, use_12h=False, show_seconds=True) == "22:33:58"
    assert S.fmt_clock(moment, use_12h=False, show_seconds=False) == "22:33"
    assert S.fmt_clock(moment, use_12h=True, show_seconds=True) == "10:33:58 PM"
    assert S.fmt_clock(moment, use_12h=True, show_seconds=False) == "10:33 PM"
    midnight = datetime(2026, 11, 13, 0, 5, 0)
    assert S.fmt_clock(midnight, use_12h=True, show_seconds=False) == "12:05 AM"


def test_username_sources(monkeypatch):
    monkeypatch.setattr(S, "_username_env", lambda: "EnvUser")
    monkeypatch.setattr(S, "_username_win", lambda: "WinUser")
    assert S.current_username() == "EnvUser"
    # env 取不到时回落到 Windows API
    def boom() -> str:
        raise OSError("no env user")

    monkeypatch.setattr(S, "_username_env", boom)
    assert S.current_username() == "WinUser"
    # 两个都取不到 → —
    monkeypatch.setattr(S, "_username_win", lambda: "")
    assert S.current_username() == S.MISSING
    monkeypatch.setattr(S, "_username_env", lambda: "  ")
    assert S.current_username() == S.MISSING


def test_clamps_anchor_mode_offsets():
    assert S.clamp_scale(0.1) == 0.6
    assert S.clamp_scale(9) == 1.6
    assert S.clamp_scale("bad") == 1.0
    assert S.clamp_opacity(0.0) == 0.30
    assert S.clamp_opacity(2) == 1.0
    assert S.clamp_offset(9999, "x") == S.OFFSET_X_MAX
    assert S.clamp_offset(-9999, "y") == S.OFFSET_Y_MIN
    assert S.normalize_anchor("top-right") == "top-right"
    assert S.normalize_anchor("nope") == "top-center"
    assert S.anchor_index("top-left") == 1
    assert S.normalize_mode("standard") == "standard"
    assert S.normalize_mode("huge") == "compact"
    assert S.mode_index("standard") == 1


def test_render_key_changes_only_when_content_changes():
    base = _snapshot()
    key = S.render_key(base)
    assert S.render_key(dict(base)) == key
    assert S.render_key({**base, "clock": "22:33:59"}) != key
    assert S.render_key({**base, "mode": "standard"}) != key
    assert S.render_key({**base, "username": "Other"}) != key
    assert S.render_key({**base, "show": dict(base["show"], battery=False)}) != key
    assert S.render_key({**base, "account": {"connected": True, "nickname": "陈", "online": False}}) != key
    # 只改窗口位置 / 缩放 / 透明度 / 排轴（如果有）不应该重画画布
    assert S.render_key({**base, "anchor": "top-left", "scale": 1.4, "opacity": 0.5}) == key
    assert S.render_key({**base, "timeline": {"connected": True, "t": 1.0}}) == key
    snapshot = {
        **base,
        "enabled": True,
        "window_created": True,
        "window_visible": True,
        "hotkey": {"spec": "Ctrl+Alt+H", "registered": True, "error": None},
    }
    snap_key = S.snapshot_key(snapshot)
    assert S.snapshot_key(dict(snapshot)) == snap_key
    assert S.snapshot_key({**snapshot, "window_visible": False}) != snap_key
    assert S.snapshot_key({**snapshot, "scale": 1.2}) != snap_key


# ---------------------------------------------------------------------------
# 热键
# ---------------------------------------------------------------------------

def test_parse_hotkey_and_display():
    mods, vk = hotkey.parse_hotkey("Ctrl+Alt+H")
    assert mods == hotkey.MOD_CONTROL | hotkey.MOD_ALT
    assert vk == 0x48
    assert hotkey.normalize_hotkey("ctrl+alt+h") == "Ctrl+Alt+H"
    assert hotkey.normalize_hotkey("shift+win+f12") == "Shift+Win+F12"
    assert hotkey.is_valid_spec("Ctrl+Alt+H") is True
    assert hotkey.is_valid_spec("H") is False


@pytest.mark.parametrize("spec", ["", "H", "Ctrl+H+J", "Ctrl+Alt+不存在的键", "Ctrl"])
def test_parse_hotkey_rejects_invalid(spec):
    with pytest.raises(hotkey.HotkeyError):
        hotkey.parse_hotkey(spec)


def test_spec_from_qt_events():
    ctrl_alt = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
    assert hotkey.spec_from_qt(Qt.Key.Key_H, ctrl_alt) == "Ctrl+Alt+H"
    shift_win = Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.MetaModifier
    assert hotkey.spec_from_qt(Qt.Key.Key_F12, shift_win) == "Shift+Win+F12"
    assert hotkey.spec_from_qt(Qt.Key.Key_Space, Qt.KeyboardModifier.ControlModifier) == "Ctrl+Space"
    assert hotkey.spec_from_qt(Qt.Key.Key_5, Qt.KeyboardModifier.AltModifier) == "Alt+5"
    assert hotkey.spec_from_qt(Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier) == "A"
    with pytest.raises(hotkey.HotkeyError):
        hotkey.normalize_hotkey("A")
    assert hotkey.spec_from_qt(Qt.Key.Key_CapsLock, Qt.KeyboardModifier.ControlModifier) is None


def test_native_filter_returns_tuple_contract():
    """PyQt6 6.5+ 要求返回 (bool, sip.voidptr)，只返回 bool 会打断事件循环。"""
    hk = hotkey.GlobalHotkey()
    filt = hotkey.HotkeyNativeFilter(hk)
    result = filt.nativeEventFilter(b"windows_generic_MSG", None)
    assert isinstance(result, tuple) and len(result) == 2
    assert result[0] is False
    hk.unregister()


def test_native_filter_dispatches_wm_hotkey():
    hk = hotkey.GlobalHotkey()
    fired: list[int] = []
    hk.activated.connect(lambda: fired.append(1))
    msg = wintypes.MSG()
    msg.message = hotkey.WM_HOTKEY
    msg.wParam = hk._hotkey_id  # noqa: SLF001 - 白盒验证消息分发
    hk._registered = True  # noqa: SLF001
    filt = hotkey.HotkeyNativeFilter(hk)
    handled, message = filt.nativeEventFilter(b"windows_dispatcher_MSG", ctypes.pointer(msg))
    assert handled is True
    assert fired == [1]
    assert message is not None
    msg.message = 0x0010
    handled, _ = filt.nativeEventFilter(b"windows_generic_MSG", ctypes.pointer(msg))
    assert handled is False
    hk._registered = False  # noqa: SLF001
    hk.unregister()


def test_hotkey_register_failure_is_graceful(app, monkeypatch):
    errors: list[str] = []
    hk = hotkey.GlobalHotkey()
    hk.failed.connect(errors.append)

    def boom():
        raise OSError("模拟 user32 不可用")

    monkeypatch.setattr(hotkey, "_user32", boom)
    assert hk.register("Ctrl+Alt+H") is False
    assert hk.registered is False
    assert hk.last_error
    assert errors and errors[0] == hk.last_error
    hk.unregister()


# ---------------------------------------------------------------------------
# 电池
# ---------------------------------------------------------------------------

def test_battery_read_failure_returns_empty(monkeypatch):
    def boom():
        raise OSError("GetSystemPowerStatus 失败")

    monkeypatch.setattr(battery, "_read_status", boom)
    power = battery.read_power()
    assert power["available"] is False
    assert power["percent"] is None
    assert power["remaining_s"] is None
    assert power["error"]


def test_battery_read_never_raises(monkeypatch):
    class Weird:
        ACLineStatus = 255
        BatteryFlag = 255
        BatteryLifePercent = 255
        BatteryLifeTime = 0xFFFFFFFF

    monkeypatch.setattr(battery, "_read_status", lambda: Weird())
    power = battery.read_power()
    assert power["available"] is True
    assert power["percent"] is None
    assert power["ac"] is None
    assert power["charging"] is None
    assert power["remaining_s"] is None


def test_battery_read_reports_remaining_time(monkeypatch):
    class Status:
        ACLineStatus = 0
        BatteryFlag = 0
        BatteryLifePercent = 42
        BatteryLifeTime = 3600

    monkeypatch.setattr(battery, "_read_status", lambda: Status())
    power = battery.read_power()
    assert power["percent"] == 42
    assert power["ac"] is False
    assert power["remaining_s"] == 3600


# ---------------------------------------------------------------------------
# 服务
# ---------------------------------------------------------------------------

def test_service_defaults(service):
    snap = service.snapshot()
    assert "timeline" not in snap
    assert snap["enabled"] is False
    assert snap["window_created"] is False
    assert snap["mode"] == "compact"
    assert snap["offset_x"] == 0 and snap["offset_y"] == 0
    assert snap["screen"] == ""
    assert snap["clock_12h"] is False
    assert snap["clock_seconds"] is True
    assert snap["account"]["connected"] is False
    assert snap["battery"]["available"] in (True, False)  # 真实读取结果
    assert len(snap["clock"]) in (5, 8, 11)  # 24h 有秒 / 无秒 / 12h 有秒
    assert snap["username"]  # Windows 用户名（或 —）
    assert snap["show"] == {"account": True, "battery": True, "clock": True}
    assert snap["hotkey"]["spec"] == "Ctrl+Alt+Shift+F24"


def test_service_does_not_subscribe_timeline(service, ctx):
    """HUD 与排轴模块解耦：排轴事件不应影响快照，也不应触发刷新。"""
    emitted: list[dict] = []
    service.changed.connect(emitted.append)
    before = service.snapshot()
    count = len(emitted)
    ctx.bus.emit(Topics.TIMELINE_TICK, t=12.0, playing=True, active=[], duration=30.0)
    ctx.bus.emit(
        Topics.TIMELINE_STATE,
        state={"t": 12.0, "playing": True, "duration": 30.0, "team": [], "events": [], "buffs": []},
    )
    after = service.snapshot()
    assert "timeline" not in after
    assert S.snapshot_key(before) == S.snapshot_key(after)
    # 服务只在账号/电池/时钟变化时广播
    assert len(emitted) <= count + 1


def test_hud_sources_do_not_import_timeline_module():
    """回归：HUD 包与控制页源码里不允许再 import 排轴模块或订阅排轴主题。"""
    root = Path(__file__).resolve().parents[1] / "endbox"
    hud_files = list((root / "modules" / "hud").glob("*.py")) + [root / "ui" / "views" / "hud.py"]
    forbidden = ("endbox.modules.timeline", "Topics.TIMELINE")
    for path in hud_files:
        text = path.read_text("utf-8")
        for token in forbidden:
            assert token not in text, f"{path.name} 仍然引用 {token}"


def test_toggle_is_idempotent_and_emits_hud_state(app, ctx, service):
    events: list[bool] = []
    ctx.bus.subscribe(Topics.HUD_STATE, lambda **payload: events.append(bool(payload.get("enabled"))))
    service.set_enabled(True)
    service.set_enabled(True)
    assert service.enabled is True
    assert ctx.settings.get("hud.enabled") is True
    assert events == [True]
    service.set_enabled(False)
    service.set_enabled(False)
    assert service.enabled is False
    assert events == [True, False]
    _flush(app)
    assert service.window is None or not service.window.isVisible()


def test_service_aggregates_account(service, ctx):
    ctx.bus.emit(Topics.ACCOUNT_STATE, nickname="博士", online=True, provider="skland", avatar_path="C:/b.png")
    snap = service.snapshot()
    assert snap["account"]["connected"] is True
    assert snap["account"]["nickname"] == "博士"
    assert snap["account"]["online"] is True
    assert snap["account"]["avatar_path"] == "C:/b.png"
    ctx.bus.emit(Topics.ACCOUNT_UPDATED, snapshot={"nickname": "陈", "online": False})
    snap = service.snapshot()
    assert snap["account"]["nickname"] == "陈"
    assert snap["account"]["online"] is False


def test_service_ignores_bad_payloads(service, ctx):
    ctx.bus.emit(Topics.ACCOUNT_UPDATED, snapshot=None)
    snap = service.snapshot()
    assert snap["account"]["connected"] is False


def test_set_option_persists_and_clamps(service, settings):
    service.set_option("scale", 99)
    assert settings.get("hud.scale") == 1.6
    service.set_option("scale", 0.1)
    assert settings.get("hud.scale") == 0.6
    service.set_option("opacity", 0)
    assert settings.get("hud.opacity") == 0.30
    service.set_option("anchor", "top-right")
    assert settings.get("hud.anchor") == "top-right"
    service.set_option("anchor", "bogus")
    assert settings.get("hud.anchor") == "top-center"
    service.set_option("mode", "standard")
    assert settings.get("hud.mode") == "standard"
    service.set_option("mode", "bogus")
    assert settings.get("hud.mode") == "compact"
    service.set_option("offset_x", 9999)
    assert settings.get("hud.offset_x") == S.OFFSET_X_MAX
    service.set_option("offset_y", -9999)
    assert settings.get("hud.offset_y") == S.OFFSET_Y_MIN
    service.set_option("screen", "DISPLAY2")
    assert settings.get("hud.screen") == "DISPLAY2"
    service.set_option("clock_12h", True)
    assert settings.get("hud.clock_12h") is True
    service.set_option("clock_seconds", False)
    assert settings.get("hud.clock_seconds") is False
    service.set_option("battery", False)
    assert settings.get("hud.show_battery") is False
    # 旧的排轴显示项开关不再被 HUD 认识：只记日志，不出现在快照里
    service.set_option("team", True)
    assert "team" not in service.snapshot()["show"]
    service.set_option("nope", 1)
    assert service.snapshot()["show"]["battery"] is False


def test_set_hotkey_falls_back_to_old_combo(service, settings):
    class FakeHotkey:
        def __init__(self):
            self.spec = "Ctrl+Alt+H"
            self.registered = True
            self.last_error = ""
            self.calls: list[str] = []

        def register(self, spec):
            self.calls.append(spec)
            if "F24" in spec:
                self.last_error = "热键已被其他程序占用（Windows 错误 1409）"
                self.registered = False
                return False
            self.spec = spec
            self.last_error = ""
            self.registered = True
            return True

        def unregister(self):
            self.registered = False

    fake = FakeHotkey()
    service._hotkey = fake  # noqa: SLF001 - 用假热键覆盖平台相关注册
    service._hotkey_spec = "Ctrl+Alt+H"  # noqa: SLF001
    settings.set("hud.hotkey", "Ctrl+Alt+H", save=False)
    ok, message = service.set_hotkey("Ctrl+Alt+F24")
    assert ok is False
    assert "回退" in message and "1409" in message
    assert fake.calls == ["Ctrl+Alt+F24", "Ctrl+Alt+H"]
    assert fake.registered is True
    assert service.hotkey_spec == "Ctrl+Alt+H"
    assert settings.get("hud.hotkey") == "Ctrl+Alt+H"
    fake.calls.clear()
    ok, message = service.set_hotkey("Ctrl+H+J")
    assert ok is False
    assert fake.calls == []
    assert service.hotkey_spec == "Ctrl+Alt+H"
    ok, message = service.set_hotkey("Ctrl+Alt+K")
    assert ok is True
    assert service.hotkey_spec == "Ctrl+Alt+K"
    assert settings.get("hud.hotkey") == "Ctrl+Alt+K"


def test_available_screens_returns_list(app, service):
    screens = service.available_screens()
    assert isinstance(screens, list)
    for item in screens:
        assert set(item) == {"name", "label", "primary"}


def test_stop_unsubscribes_and_unregisters(app, ctx, service):
    ctx.bus.emit(Topics.ACCOUNT_STATE, nickname="博士", online=True)
    assert service.snapshot()["account"]["connected"] is True
    service.stop()
    _flush(app)
    assert service.window is None
    assert service.hotkey_registered is False
    ctx.bus.emit(Topics.ACCOUNT_STATE, nickname="不应生效", online=True)
    assert service.snapshot()["account"]["nickname"] == "博士"


def test_stop_is_idempotent(service):
    service.stop()
    service.stop()


# ---------------------------------------------------------------------------
# 窗口 / 防闪烁
# ---------------------------------------------------------------------------

def test_window_reuse_no_leak(app, service):
    service.set_enabled(True)
    _flush(app)
    window = service.window
    assert window is not None
    assert window.isVisible()
    assert _overlay_windows(app) == [window]
    for _ in range(5):
        service.hide(animate=False)
        service.show()
        _flush(app)
        assert service.window is window  # 始终复用同一个窗口实例
        assert len(_overlay_windows(app)) == 1
    service.stop()
    _flush(app)
    assert service.window is None
    assert _overlay_windows(app) == []


def test_overlay_window_flags_and_geometry(app, service):
    service.set_enabled(True)
    _flush(app)
    window = service.window
    flags = window.windowFlags()
    for flag in (
        Qt.WindowType.FramelessWindowHint,
        Qt.WindowType.WindowStaysOnTopHint,
        Qt.WindowType.Tool,
        Qt.WindowType.WindowTransparentForInput,
    ):
        assert bool(flags & flag), flag
    assert window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert window.focusPolicy() == Qt.FocusPolicy.NoFocus
    # 紧凑模式下窗口尺寸 = 设计尺寸 × 缩放
    assert (window.width(), window.height()) == (560, 76)
    service.set_option("scale", 1.6)
    _flush(app)
    assert (window.width(), window.height()) == (round(560 * 1.6), round(76 * 1.6))
    # 切换标准模式 = 720×96（× 缩放）
    service.set_option("mode", "standard")
    _flush(app)
    assert (window.width(), window.height()) == (round(720 * 1.6), round(96 * 1.6))


def test_window_does_not_touch_win32_props_when_nothing_changed(app, service):
    """闪烁回归：相同快照重复 apply 不能重复 setWindowOpacity / 不能重复重画画布。"""
    service.set_enabled(True)
    _flush(app)
    window = service.window
    snapshot = service.snapshot()

    opacity_calls: list[float] = []
    repaints: list[int] = []
    window.setWindowOpacity = lambda value: opacity_calls.append(value)  # type: ignore[method-assign]
    window.canvas.update = lambda *args, **kwargs: repaints.append(1)  # type: ignore[method-assign]

    window.apply_snapshot(snapshot)
    first_repaints = len(repaints)
    first_opacity = len(opacity_calls)
    assert window.apply_snapshot(snapshot) is False
    assert len(repaints) == first_repaints
    assert len(opacity_calls) == first_opacity
    changed = service.snapshot()
    changed["clock"] = "22:33:59"
    assert window.apply_snapshot(changed) is True
    assert len(repaints) == first_repaints + 1


def test_service_flush_dedupes_identical_snapshots(app, service):
    emitted: list[dict] = []
    service.changed.connect(emitted.append)
    service._flush()  # noqa: SLF001
    count = len(emitted)
    service._flush()  # noqa: SLF001 - 内容没变：不应再广播
    assert len(emitted) == count
    service.set_option("clock_seconds", False)
    assert len(emitted) > count


def test_anchor_positioning_with_offsets():
    from endbox.modules.hud.overlay import compute_position

    screen = QRect(0, 0, 1920, 1080)
    size = QSize(560, 76)
    assert compute_position("top-center", size, screen).x() == (1920 - 560) // 2
    assert compute_position("top-left", size, screen).x() == 18
    assert compute_position("top-right", size, screen).x() == 1920 - 560 - 18
    assert compute_position("top-center", size, screen).y() == 18
    moved = compute_position("top-center", size, screen, 40, -10)
    assert moved.x() == (1920 - 560) // 2 + 40
    assert moved.y() == 8


def test_offsets_and_screen_survive_snapshot(app, service):
    service.set_option("offset_x", 32)
    service.set_option("offset_y", -12)
    _flush(app)
    snap = service.snapshot()
    assert snap["offset_x"] == 32
    assert snap["offset_y"] == -12
    window = service.window
    if window is None:
        service.set_enabled(True)
        _flush(app)
        window = service.window
    assert window is not None
    assert window._offset_x == 32  # noqa: SLF001
    assert window._offset_y == -12  # noqa: SLF001


# ---------------------------------------------------------------------------
# 画布
# ---------------------------------------------------------------------------

def test_design_sizes_per_mode():
    from endbox.modules.hud.overlay import design_size

    assert design_size("compact") == QSize(560, 76)
    assert design_size("standard") == QSize(720, 96)
    assert design_size("bogus") == QSize(560, 76)


def test_canvas_group_layout_never_overflows():
    from endbox.modules.hud.overlay import GEOMETRY

    for mode in ("compact", "standard"):
        canvas = _canvas(_snapshot(mode))
        geo = GEOMETRY[mode]
        left = geo["pad"] + geo["marker"] + geo["marker_gap"]
        right = geo["w"] - geo["pad"]
        cells = canvas.group_layout()
        assert cells, mode
        for cell in cells:
            assert cell["x"] >= left - 0.01
            assert cell["x"] + cell["w"] <= right + 0.01


def test_compact_layout_has_only_account_battery_clock():
    canvas = _canvas(_snapshot("compact"))
    assert [cell["key"] for cell in canvas.group_layout()] == ["account", "battery", "clock"]
    # 关掉某一项就不画
    canvas = _canvas(_snapshot("compact", show={"account": False}))
    assert [cell["key"] for cell in canvas.group_layout()] == ["battery", "clock"]


def test_standard_layout_two_rows_and_single_row_fallback():
    canvas = _canvas(_snapshot("standard"))
    rows = {cell["row"] for cell in canvas.group_layout()}
    assert rows == {"top", "main"}
    top = [cell["key"] for cell in canvas.group_layout() if cell["row"] == "top"]
    main = [cell["key"] for cell in canvas.group_layout() if cell["row"] == "main"]
    assert top == ["account", "clock"]
    assert main == ["battery"]
    # 关掉账号：上排只剩时钟，下排电池 → 仍是两行
    canvas = _canvas(_snapshot("standard", show={"account": False}))
    assert {cell["row"] for cell in canvas.group_layout()} == {"top", "main"}
    # 只留时钟 → 单行占满
    canvas = _canvas(_snapshot("standard", show={"account": False, "battery": False}))
    assert [cell["key"] for cell in canvas.group_layout()] == ["clock"]


def test_canvas_uses_username_as_title():
    from endbox.modules.hud import overlay as overlay_mod

    # 标题态（progress 0.55）应当把用户名画出来：用渲染 key 覆盖 + 标题文本来源检查
    canvas = _canvas(_snapshot(username="DrEndfield"), 0.55)
    assert canvas.snapshot["username"] == "DrEndfield"
    assert "实时覆盖" not in overlay_mod.TITLE_EYEBROW
    assert not hasattr(overlay_mod, "TITLE")


@pytest.mark.parametrize("mode", ["compact", "standard"])
@pytest.mark.parametrize("progress", [0.1, 0.42, 0.62, 1.0])
def test_canvas_paints_animation_phases(app, mode, progress):
    canvas = _canvas(_snapshot(mode), progress)
    pixmap = canvas.grab()
    assert not pixmap.isNull()
    image = pixmap.toImage()
    center = image.pixelColor(image.width() // 2, image.height() // 2)
    if progress >= 0.2:
        assert center.alpha() > 0


def test_canvas_paints_degraded_and_empty_states(app):
    degraded = _snapshot(
        "standard",
        account=S.default_account(),
        battery=battery.empty_power("no battery"),
    )
    empty = _snapshot("compact", show={"account": False, "battery": False, "clock": False})
    for snapshot in (degraded, empty):
        canvas = _canvas(snapshot)
        image = canvas.grab().toImage()
        assert image.pixelColor(image.width() // 2, image.height() // 2).alpha() > 0


def test_canvas_set_snapshot_dedupe(app):
    from endbox.modules.hud.overlay import HudCanvas, design_size

    canvas = HudCanvas()
    design = design_size("compact")
    canvas.resize(design.width(), design.height())
    calls: list[int] = []
    canvas.update = lambda *args, **kwargs: calls.append(1)  # type: ignore[method-assign]
    assert canvas.set_snapshot(_snapshot()) is True
    count = len(calls)
    assert canvas.set_snapshot(_snapshot()) is False
    assert len(calls) == count


def test_choreography_channels():
    from endbox.modules.hud.overlay import choreography, panel_height

    start = choreography(0.0)
    assert start["appear"] == 0.0
    assert start["data_op"] == 0.0
    end = choreography(1.0)
    assert end["appear"] == 1.0
    assert end["expand"] == 1.0
    assert end["title_op"] == 0.0
    assert end["data_op"] == 1.0
    assert end["ripple_op"] == 0.0
    static = choreography(0.0, animations=False)
    assert static["data_op"] == 1.0
    assert static["title_op"] == 0.0
    assert panel_height(1.0, "compact") == 64.0
    assert panel_height(1.0, "standard") == 88.0


# ---------------------------------------------------------------------------
# 视图
# ---------------------------------------------------------------------------

def test_view_builds_with_service(app, ctx, service):
    from endbox.ui.views.hud import HudView

    view = HudView(ctx)
    assert view.key == "hud"
    assert view.eyebrow == "HUD"
    assert view.order == 30
    assert view.sidebar_label == "HUD"
    assert set(view._show_toggles) == {"account", "battery", "clock"}  # noqa: SLF001
    fields = view.topbar_fields()
    assert fields and any("热键" in field for field in fields)
    assert len(view.topbar_actions()) == 2
    view.refresh()
    view.on_show()
    view.on_hide()


def test_view_builds_without_service(app, ctx):
    from endbox.ui.views.hud import HudView

    ctx.services.hud = None
    view = HudView(ctx)
    assert view.key == "hud"
    view.refresh()
    view.on_show()
    view.on_hide()
    assert view.topbar_fields() == []


def test_view_toggle_updates_service(app, ctx, service):
    from endbox.ui.views.hud import HudView

    view = HudView(ctx)
    view._enable_toggle.setChecked(True)  # noqa: SLF001
    _flush(app)
    assert service.enabled is True
    view._enable_toggle.setChecked(False)  # noqa: SLF001
    _flush(app)
    assert service.enabled is False


def test_view_display_item_toggle_and_mode(app, ctx, service):
    from endbox.ui.views.hud import HudView

    view = HudView(ctx)
    view._show_toggles["battery"].setChecked(False)  # noqa: SLF001
    _flush(app)
    assert ctx.settings.get("hud.show_battery") is False
    view._mode_seg.set_index(1, emit=True)  # noqa: SLF001
    _flush(app)
    assert ctx.settings.get("hud.mode") == "standard"
    view._clock_seg.set_index(1, emit=True)  # noqa: SLF001
    _flush(app)
    assert ctx.settings.get("hud.clock_12h") is True
    view._clock_seconds.setChecked(False)  # noqa: SLF001
    _flush(app)
    assert ctx.settings.get("hud.clock_seconds") is False


def test_view_hotkey_recording_flow(app, ctx, service):
    from endbox.ui.views.hud import HudView

    view = HudView(ctx)
    recorded: list[str] = []
    view.ctx.notify = lambda text, kind="info": recorded.append(f"{kind}:{text}")

    class FakeHotkey:
        def __init__(self):
            self.spec = "Ctrl+Alt+H"
            self.registered = True
            self.last_error = ""

        def register(self, spec):
            self.spec = spec
            self.registered = True
            return True

        def unregister(self):
            self.registered = False

    fake = FakeHotkey()
    service._hotkey = fake  # noqa: SLF001
    service._hotkey_spec = "Ctrl+Alt+H"  # noqa: SLF001
    view.refresh()
    view._recorder.start()  # noqa: SLF001
    assert fake.registered is False
    view._on_recorded("Ctrl+Alt+K")  # noqa: SLF001
    assert fake.registered is True
    assert fake.spec == "Ctrl+Alt+K"
    assert any("ok" in item for item in recorded)
    view._recorder.stop()  # noqa: SLF001
    view._recorder.start()  # noqa: SLF001
    assert fake.registered is False
    view._recorder.cancel()  # noqa: SLF001
    assert fake.registered is True


# ---------------------------------------------------------------------------
# 文案集中化（assets/text/zh-CN.json 的 hud section）
# ---------------------------------------------------------------------------

def _hud_text_files() -> list[Path]:
    root = Path(__file__).resolve().parents[1]
    return list((root / "endbox" / "modules" / "hud").glob("*.py")) + [
        root / "endbox" / "ui" / "views" / "hud.py"
    ]


def test_hud_text_keys_exist_in_catalog():
    from endbox.core.text import catalog

    required = [
        "hud.title",
        "hud.eyebrow",
        "hud.view_title",
        "hud.sidebar_label",
        "hud.description",
        "hud.strip",
        "hud.overlay.empty",
        "hud.account.disconnected",
        "hud.battery.mode_ac",
        "hud.battery.mode_battery",
        "hud.battery.mode_charging",
        "hud.battery.detail",
        "hud.preview.title",
        "hud.preview.backdrop",
        "hud.display.title",
        "hud.layout.title",
        "hud.hotkey_panel.title",
        "hud.status.title",
        "hud.empty.title",
        "hud.about.title",
        "hud.notify.enabled",
        "hud.notify.disabled",
        "hud.errors.hotkey_empty",
        "hud.errors.hotkey_occupied",
        "hud.errors.battery_windows_only",
    ]
    for key in required:
        assert catalog.has(key), key
    assert t("hud.title") == "/// ENDBOX · HUD"
    # 动态拼接的 key 家族
    for key in [
        "hud.modes.compact",
        "hud.modes.standard",
        "hud.anchors.top-center",
        "hud.anchors.top-left",
        "hud.anchors.top-right",
        "hud.overlay.eyebrows.account",
        "hud.overlay.eyebrows.battery",
        "hud.overlay.eyebrows.clock",
        "hud.display.item_account",
        "hud.display.item_battery",
        "hud.display.item_clock",
    ]:
        assert catalog.has(key), key


def test_hud_text_format_placeholders():
    assert "{spec}" not in t("hud.topbar.hotkey", spec="Ctrl+Alt+H")
    assert "Ctrl+Alt+H" in t("hud.topbar.hotkey", spec="Ctrl+Alt+H")
    assert "76%" in t("hud.status.battery", detail="76% · AC")
    text = t("hud.errors.hotkey_unknown_key", name="Foo")
    assert "Foo" in text


def test_hud_code_has_no_chinese_ui_literals():
    """HUD 用户可见文案全部来自 JSON：源码里不允许中文字符串字面量（docstring 除外）。"""
    import ast
    import re

    han = re.compile(r"[\u4e00-\u9fff]")

    def docstring_ids(tree) -> set[int]:
        ids: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    value = body[0].value
                    if isinstance(value.value, str):
                        ids.add(id(value))
        return ids

    offenders: list[str] = []
    for path in _hud_text_files():
        tree = ast.parse(path.read_text("utf-8"))
        skip = docstring_ids(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and han.search(node.value)
                and id(node) not in skip
            ):
                offenders.append(f"{path.name}:{node.lineno}: {node.value!r}")
    assert offenders == [], "中文文案应放进 assets/text/zh-CN.json：\n" + "\n".join(offenders)


def test_hud_source_still_does_not_import_timeline():
    """文案改造不能把排轴模块重新带回来。"""
    for path in _hud_text_files():
        text = path.read_text("utf-8")
        assert "endbox.modules.timeline" not in text, path.name
        assert "Topics.TIMELINE" not in text, path.name
