# -*- coding: utf-8 -*-
"""HUD 服务：覆盖层窗口的生命周期、全局热键、数据聚合与设置。

HUD 只负责 **账号 / 电池 / 时钟**（+ 标题里的 Windows 用户名）。
排轴运行态由排轴模块自己的提示条负责，两者互相独立：本服务**不订阅**排轴事件。

职责：
  * 持有唯一的 :class:`~endbox.modules.hud.overlay.HudWindow`（重复 show/hide 不新建窗口）；
  * 注册全局热键 ``Ctrl+Alt+H``（可录制替换，失败回退旧组合）；
  * 订阅 ``account.state`` / ``account.updated``；
  * 每秒刷新时钟、每 5 秒读一次电池（ctypes ``GetSystemPowerStatus``）；
  * 把设置（模式 / 位置 / 缩放 / 透明度 / 显示项 / 屏幕）合并成一份快照给窗口和控制页。

防闪烁：``_flush`` 用 ``state.snapshot_key`` 去重，内容没变就不广播；
窗口内部再用 ``state.render_key`` 去重画布，并只在尺寸/透明度真的变化时动 Win32 属性。

所有数字都是真实数据；账号未连接时快照里是 ``connected=False``，界面显示"未连接"。
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication

from endbox.app_context import AppContext
from endbox.core.event_bus import Topics
from endbox.core.log import get_logger
from endbox.core.text import t
from endbox.modules.hud import battery, state as S
from endbox.modules.hud.hotkey import GlobalHotkey, HotkeyError, normalize_hotkey
from endbox.theme.tokens import theme

log = get_logger("hud.service")

CLOCK_INTERVAL_MS = 1000
POWER_EVERY_TICKS = 5
EMIT_THROTTLE_MS = 60
PREVIEW_HIDE_MS = 2400

# 显示项开关：短名 → settings 路径
SHOW_KEYS = {
    "account": "hud.show_account",
    "battery": "hud.show_battery",
    "clock": "hud.show_clock",
}

ACCOUNT_FIELDS = ("nickname", "online", "provider", "avatar_path", "roles", "last_sync", "error")


class HudService(QObject):
    """HUD 后台服务。由 ``endbox.app._init_services`` 通过 ``create_service(ctx)`` 创建。"""

    changed = pyqtSignal(dict)

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.settings = ctx.settings
        self.bus = ctx.bus

        self._window = None
        self._enabled = bool(self.settings.get("hud.enabled", False))
        self._account = S.default_account()
        self._power = battery.read_power()
        self._username = S.current_username()
        self._clock = self._clock_text()
        self._ticks = 0
        self._emit_pending = False
        self._last_emit_key: tuple | None = None
        self._stopped = False
        self._subs: list[Any] = []

        # 全局热键
        self._hotkey_spec = str(self.settings.get("hud.hotkey", "Ctrl+Alt+H") or "Ctrl+Alt+H")
        self._hotkey = GlobalHotkey(self)
        self._hotkey.activated.connect(self._on_hotkey)
        self._hotkey.failed.connect(self._on_hotkey_failed)
        self.register_hotkey()

        # 预览计时（"预览覆盖层"按一下显示 2.4 秒再自动收回）
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._end_preview)

        self._subscribe()
        self._prime_from_services()

        # 主题切换后覆盖层立刻重画（它不在主窗口的 restyle 树里）
        self._theme_connected = False
        try:
            theme.changed.connect(self._on_theme_changed)
            self._theme_connected = True
        except Exception:  # noqa: BLE001
            log.exception("connect theme.changed failed")

        self._timer = QTimer(self)
        self._timer.setInterval(CLOCK_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        if self._enabled:
            self.show()

    # ------------------------------------------------------------------
    # 契约属性
    # ------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def hotkey_spec(self) -> str:
        return self._hotkey_spec

    @property
    def hotkey_registered(self) -> bool:
        return self._hotkey.registered

    @property
    def hotkey_error(self) -> str:
        return self._hotkey.last_error

    @property
    def username(self) -> str:
        return self._username

    @property
    def window(self):
        """当前覆盖层窗口（没有创建时是 None）。"""
        return self._window

    def animations_enabled(self) -> bool:
        return bool(self.settings.get("app.animations", True))

    def _clock_text(self) -> str:
        return S.fmt_clock(
            use_12h=bool(self.settings.get("hud.clock_12h", False)),
            show_seconds=bool(self.settings.get("hud.clock_seconds", True)),
        )

    # ------------------------------------------------------------------
    # 开关
    # ------------------------------------------------------------------
    def set_enabled(self, on: bool) -> None:
        on = bool(on)
        self._preview_timer.stop()
        self.settings.set("hud.enabled", on)
        changed = on != self._enabled
        self._enabled = on
        if on:
            self.show()
        else:
            self.hide()
        if changed:
            self.bus.emit(Topics.HUD_STATE, enabled=on)
        self._schedule_emit()

    def show(self) -> None:
        """显示覆盖层并播放出现动画（窗口复用，不新建）。"""
        self._preview_timer.stop()
        window = self._ensure_window()
        if window is None:
            return
        window.show_overlay(self.snapshot())
        self._schedule_emit()

    def hide(self, *, animate: bool = True) -> None:
        """收起覆盖层（窗口保留，下次直接复用）。"""
        self._preview_timer.stop()
        if self._window is not None:
            self._window.hide_overlay(animate=animate)
        self._schedule_emit()

    def toggle(self) -> None:
        self.set_enabled(not self._enabled)
        self.ctx.notify(t("hud.notify.hud_on" if self._enabled else "hud.notify.hud_off"), "ok")

    def preview(self, hold_ms: int = PREVIEW_HIDE_MS) -> None:
        """预览：临时显示覆盖层并重播一次入场动画，未启用时自动收回。"""
        window = self._ensure_window()
        if window is None:
            return
        window.show_overlay(self.snapshot())
        if not self._enabled:
            self._preview_timer.start(max(600, int(hold_ms)))
        self._schedule_emit()

    def _end_preview(self) -> None:
        if not self._enabled and self._window is not None:
            self._window.hide_overlay()
            self._schedule_emit()

    def stop(self) -> None:
        """退出清理：注销热键、停掉定时器、释放窗口。"""
        if self._stopped:
            return
        self._stopped = True
        self._timer.stop()
        self._preview_timer.stop()
        if self._theme_connected:
            try:
                theme.changed.disconnect(self._on_theme_changed)
            except Exception:  # noqa: BLE001
                pass
            self._theme_connected = False
        for sub in self._subs:
            try:
                sub.cancel()
            except Exception:  # noqa: BLE001
                pass
        self._subs = []
        try:
            self._hotkey.unregister()
        except Exception:  # noqa: BLE001
            log.exception("hotkey unregister failed")
        if self._window is not None:
            try:
                self._window.stop()
            except Exception:  # noqa: BLE001
                log.exception("hud window stop failed")
            self._window = None
        log.info("HUD service stopped")

    # ------------------------------------------------------------------
    # 设置
    # ------------------------------------------------------------------
    def set_option(self, name: str, value: Any) -> None:
        """改一项 HUD 设置（挡位由 state 模块夹紧，非法值不会写坏设置）。"""
        if name == "scale":
            value = S.clamp_scale(value)
            self.settings.set("hud.scale", value)
        elif name == "opacity":
            value = S.clamp_opacity(value)
            self.settings.set("hud.opacity", value)
        elif name == "anchor":
            value = S.normalize_anchor(value)
            self.settings.set("hud.anchor", value)
        elif name == "mode":
            value = S.normalize_mode(value)
            self.settings.set("hud.mode", value)
        elif name == "offset_x":
            value = S.clamp_offset(value, "x")
            self.settings.set("hud.offset_x", value)
        elif name == "offset_y":
            value = S.clamp_offset(value, "y")
            self.settings.set("hud.offset_y", value)
        elif name == "screen":
            self.settings.set("hud.screen", str(value or ""))
        elif name in ("clock_12h", "clock_seconds"):
            self.settings.set("hud." + name, bool(value))
            self._clock = self._clock_text()
        elif name in SHOW_KEYS:
            self.settings.set(SHOW_KEYS[name], bool(value))
        else:
            log.warning("unknown hud option: %s", name)
            return
        self._flush()

    def available_screens(self) -> list[dict]:
        """当前系统里的显示器（控制页下拉框用）。没有 Qt 应用时返回空列表。"""
        try:
            screens = QGuiApplication.screens()
            primary = QGuiApplication.primaryScreen()
        except Exception:  # noqa: BLE001
            return []
        out: list[dict] = []
        for index, screen in enumerate(screens):
            geometry = screen.availableGeometry()
            out.append(
                {
                    "name": screen.name(),
                    "label": f"{index + 1} · {screen.name()} · {geometry.width()}×{geometry.height()}",
                    "primary": screen is primary,
                }
            )
        return out

    # ------------------------------------------------------------------
    # 热键
    # ------------------------------------------------------------------
    def register_hotkey(self, spec: str | None = None) -> bool:
        """注册 / 重新注册全局热键。返回 False 时 :attr:`hotkey_error` 有原因。"""
        target = spec or str(self.settings.get("hud.hotkey", self._hotkey_spec) or self._hotkey_spec)
        ok = self._hotkey.register(target)
        self._hotkey_spec = self._hotkey.spec or str(target)
        if ok:
            self.settings.set("hud.hotkey", self._hotkey_spec)
        else:
            log.warning("hotkey unavailable: %s", self._hotkey.last_error)
        self._flush()
        return ok

    def suspend_hotkey(self) -> None:
        """录制新组合前先注销，否则系统会吞掉按键、控制页收不到。"""
        self._hotkey.unregister()
        self._flush()

    def resume_hotkey(self) -> bool:
        """录制取消后用旧组合恢复注册。"""
        if self._hotkey.registered:
            return True
        ok = self._hotkey.register(self._hotkey_spec)
        self._flush()
        return ok

    def set_hotkey(self, spec: str) -> tuple[bool, str]:
        """应用新热键；失败会自动回退旧组合。返回 ``(是否成功, 给用户的话)``。"""
        try:
            candidate = normalize_hotkey(spec)
        except HotkeyError as exc:
            # 连语法都不对：不注销现有热键，直接提示
            return False, str(exc)
        old = self._hotkey.spec or self._hotkey_spec
        if candidate == old and self._hotkey.registered:
            return True, ""
        ok = self._hotkey.register(candidate)
        if ok:
            self._hotkey_spec = self._hotkey.spec
            self.settings.set("hud.hotkey", self._hotkey_spec)
            self._flush()
            return True, ""
        error = self._hotkey.last_error or t("hud.errors.register_failed")
        restored = self._hotkey.register(old) if old else False
        if restored:
            self._hotkey_spec = self._hotkey.spec
            message = t("hud.errors.hotkey_fallback_ok", error=error, spec=self._hotkey_spec)
        else:
            self._hotkey_spec = old
            message = t("hud.errors.hotkey_fallback_failed", error=error, old=old)
        self._flush()
        return False, message

    # ------------------------------------------------------------------
    # 快照
    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        show = {
            key: bool(self.settings.get(path, S.DEFAULT_SHOW.get(key, True)))
            for key, path in SHOW_KEYS.items()
        }
        return {
            "enabled": self._enabled,
            "window_created": self._window is not None,
            "window_visible": bool(self._window is not None and self._window.isVisible()),
            "hotkey": {
                "spec": self._hotkey_spec,
                "registered": self._hotkey.registered,
                "error": self._hotkey.last_error or None,
            },
            "account": dict(self._account),
            "battery": dict(self._power),
            "clock": self._clock,
            "username": self._username,
            "anchor": S.normalize_anchor(self.settings.get("hud.anchor", "top-center")),
            "offset_x": S.clamp_offset(self.settings.get("hud.offset_x", 0), "x"),
            "offset_y": S.clamp_offset(self.settings.get("hud.offset_y", 0), "y"),
            "screen": str(self.settings.get("hud.screen") or ""),
            "mode": S.normalize_mode(self.settings.get("hud.mode", "compact")),
            "scale": S.clamp_scale(self.settings.get("hud.scale", 1.0)),
            "opacity": S.clamp_opacity(self.settings.get("hud.opacity", 0.96)),
            "clock_12h": bool(self.settings.get("hud.clock_12h", False)),
            "clock_seconds": bool(self.settings.get("hud.clock_seconds", True)),
            "show": show,
            "animations": self.animations_enabled(),
        }

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------
    def _subscribe(self) -> None:
        handlers = (
            (Topics.ACCOUNT_STATE, self._on_account_state),
            (Topics.ACCOUNT_UPDATED, self._on_account_updated),
            (Topics.HUD_TOGGLE, self._on_hud_toggle),
        )
        for topic, handler in handlers:
            try:
                self._subs.append(self.bus.subscribe(topic, handler))
            except Exception:  # noqa: BLE001
                log.exception("subscribe %s failed", topic)

    def _prime_from_services(self) -> None:
        """账号服务已经存在时先取一次状态，避免 HUD 打开后空白。"""
        account = getattr(self.ctx.services, "account", None)
        if account is not None:
            data = getattr(account, "state", None)
            if isinstance(data, dict):
                self._apply_account(data)
                self._account["connected"] = True

    def _on_account_state(self, **payload: Any) -> None:
        self._apply_account(payload)
        self._account["connected"] = True
        self._schedule_emit()

    def _on_account_updated(self, snapshot: Any = None, **payload: Any) -> None:
        data = snapshot if isinstance(snapshot, dict) else payload.get("snapshot")
        if isinstance(data, dict):
            self._apply_account(data)
            self._account["connected"] = True
            self._schedule_emit()

    def _apply_account(self, data: dict) -> None:
        for key in ACCOUNT_FIELDS:
            if key in data:
                self._account[key] = data[key]

    def _on_hud_toggle(self, **_payload: Any) -> None:
        self.toggle()

    def _on_hotkey(self) -> None:
        self.toggle()

    def _on_hotkey_failed(self, message: str) -> None:
        self._schedule_emit()
        try:
            self.ctx.notify(t("hud.notify.hotkey_unavailable", message=message), "warn")
        except Exception:  # noqa: BLE001
            pass

    def _on_theme_changed(self, _mode: str = "") -> None:
        if self._window is not None:
            try:
                self._window.apply_snapshot(self.snapshot())
            except Exception:  # noqa: BLE001
                log.exception("refresh hud theme failed")

    # ------------------------------------------------------------------
    # 定时器
    # ------------------------------------------------------------------
    def _tick(self) -> None:
        self._clock = self._clock_text()
        self._ticks += 1
        if self._ticks % POWER_EVERY_TICKS == 0:
            self._power = battery.read_power()
        self._schedule_emit()

    def _schedule_emit(self) -> None:
        if self._stopped or self._emit_pending:
            return
        self._emit_pending = True
        QTimer.singleShot(EMIT_THROTTLE_MS, self._flush)

    def _flush(self) -> None:
        self._emit_pending = False
        if self._stopped:
            return
        snapshot = self.snapshot()
        key = S.snapshot_key(snapshot)
        if key == self._last_emit_key:
            return
        self._last_emit_key = key
        if self._window is not None:
            try:
                self._window.apply_snapshot(snapshot)
            except Exception:  # noqa: BLE001
                log.exception("apply hud snapshot failed")
        self.changed.emit(snapshot)

    # ------------------------------------------------------------------
    # 窗口
    # ------------------------------------------------------------------
    def _ensure_window(self):
        if self._window is None:
            from endbox.modules.hud.overlay import HudWindow

            try:
                window = HudWindow(self)
            except Exception:  # noqa: BLE001 - 创建失败要能降级，不能崩主程序
                log.exception("hud window create failed")
                return None
            window.dismissed.connect(self._on_window_dismissed)
            self._window = window
        return self._window

    def _on_window_dismissed(self) -> None:
        self._schedule_emit()


def create_service(ctx: AppContext) -> HudService:
    """模块工厂：``endbox.app`` 启动时调用。"""
    return HudService(ctx)
