# -*- coding: utf-8 -*-
"""运行态全局按键捕捉适配层。

优先使用 Lead/account-dev 的输入服务（``ctx.services.input`` 或
``endbox.core.input``，接口就绪后自动接入）；未就绪时用 Windows
``GetAsyncKeyState`` 轮询兜底（不注入、不抢焦点、不吞按键）。
测试/无输入环境用 ``feed()`` 注入 token（mock）。

真实键位（对齐游戏内设置）：
- 数字 1~4 短按（松开时按住时长 < hold_ms）= 战技；长按 = 终结技（``HOLD+n``）；
- E = 连携（全队共用，输入服务里 E 是"修饰键"不产 token，这里从 raw 的 vk 补发）；
- F1~F4 = 切换干员（可在键位设置里改，默认 F1~F4）；
- 重击没有独立键盘键（普攻连段末段 / 长按左键），轴上只做提示。

适配层对外只发规范 token：``1`` / ``HOLD+4`` / ``E`` / ``F1`` / ``F8`` …
旧版 ``Q+1`` / ``E+3`` / ``ALT+2`` 仍可被解析（保留兼容），但运行态不再接受。
"""
from __future__ import annotations

import ctypes
import sys
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from endbox.core.log import get_logger
from endbox.modules.timeline.runtime import (
    DEFAULT_KEYS,
    key_token_for,
    keys_from_settings,
    normalize_key,
    normalize_token,
    switch_key,
)
from endbox.core.text import t as tr

log = get_logger("modules.timeline.input")

POLL_MS = 28

_VK_SPECIAL = {
    "ALT": 0x12,
    "CTRL": 0x11,
    "SHIFT": 0x10,
    "SPACE": 0x20,
    "TAB": 0x09,
    "ENTER": 0x0D,
    "ESC": 0x1B,
}


def vk_for(key: str) -> int | None:
    key = normalize_key(key, "")
    if not key:
        return None
    if key in _VK_SPECIAL:
        return _VK_SPECIAL[key]
    if len(key) == 1 and key.isalnum():
        return ord(key)
    if key.startswith("F") and key[1:].isdigit():
        number = int(key[1:])
        if 1 <= number <= 24:
            return 0x70 + number - 1
    return None


class InputAdapter(QObject):
    """按键 token 适配器。"""

    token = pyqtSignal(str)

    def __init__(self, ctx=None, *, backend: str | None = None, poll_ms: int = POLL_MS, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.backend = backend or "auto"
        self.poll_ms = max(10, int(poll_ms))
        self.last_error: str | None = None
        self._keys: dict = dict(DEFAULT_KEYS)
        self._active = False
        self._service = None
        self._service_sub = None
        self._service_signal = None
        self._service_started = False
        self._user32 = None
        self._down: dict[int, bool] = {}
        self._digit_down: dict[int, float] = {}
        self._held_digits: set[int] = set()
        self._service_raw = None
        self._raw_available = False
        self._hold_ms = 280
        self._gamepad = "off"
        self._dedupe_ms = 60
        self._recent: dict[str, float] = {}
        self._timer = QTimer(self)
        self._timer.setInterval(self.poll_ms)
        self._timer.timeout.connect(self._poll)

    # -- 配置 ---------------------------------------------------------------
    def set_keys(self, keys: dict) -> None:
        self._keys = keys_from_settings(keys)

    def set_runtime_options(self, *, hold_ms: int | None = None, gamepad: str | None = None) -> None:
        """运行设置里的长按阈值 / 手柄模式（InputService 可用时同步给它）。"""
        import time as _time
        if hold_ms is not None:
            try:
                self._hold_ms = max(80, min(2000, int(hold_ms)))
            except (TypeError, ValueError):
                pass
        if gamepad is not None:
            self._gamepad = str(gamepad or "off").lower()
        service = self._service
        if service is not None and self._service_started:
            for name, value in (("set_hold_ms", self._hold_ms), ("set_gamepad", self._gamepad)):
                method = getattr(service, name, None)
                if callable(method):
                    try:
                        method(value)
                    except Exception:  # noqa: BLE001
                        pass

    @property
    def keys(self) -> dict:
        return dict(self._keys)

    def _switch_keys(self) -> list[tuple[int, str]]:
        """(槽位, 键位)：切换干员的 F1~F4（可在键位设置里改）。"""
        return [(slot, switch_key(self._keys, slot)) for slot in range(4)]

    @property
    def available(self) -> bool:
        return self._active and self.backend in ("service", "poll", "mock")

    @property
    def status_text(self) -> str:
        if self.last_error:
            return self.last_error
        mapping = {
            "service": tr("timeline.input.status_service"),
            "poll": tr("timeline.input.status_poll"),
            "mock": tr("timeline.input.status_mock"),
            "none": tr("timeline.input.status_none"),
            "auto": tr("timeline.input.status_auto"),
        }
        return mapping.get(self.backend, self.backend)

    # -- 生命周期 -----------------------------------------------------------
    def start(self) -> bool:
        if self._active:
            return True
        self.last_error = None
        if self.backend == "mock":
            self._active = True
            log.info("输入适配层使用 mock 注入")
            return True
        service_ok = False
        if self.backend in ("auto", "service"):
            service_ok = self._try_service_backend()
        poll_ok = False
        # 双通道：即使输入服务订阅成功，也保留 GetAsyncKeyState 轮询兜底，
        # 防止钩子因 UIPI/非交互桌面/前台权限收不到键时“静默失效”。
        if sys.platform.startswith("win") and self.backend in ("auto", "service", "poll"):
            try:
                self._user32 = ctypes.WinDLL("user32", use_last_error=True)
                self._user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
                self._user32.GetAsyncKeyState.restype = ctypes.c_short
                self._timer.start()
                poll_ok = True
            except Exception as exc:  # noqa: BLE001
                log.warning("GetAsyncKeyState 兜底启动失败：%s", exc)
        if service_ok:
            self.backend = "service"
            self._active = True
            log.info("输入服务已接入（%s）", "service+poll" if poll_ok else "service")
            return True
        if poll_ok:
            self.backend = "poll"
            self._active = True
            log.info("输入适配层使用 GetAsyncKeyState 轮询（%sms）", self.poll_ms)
            return True
        self.backend = "none"
        self.last_error = tr("timeline.input.unsupported")
        return False

    def stop(self) -> None:
        self._timer.stop()
        if self._service_sub is not None:
            cancel = getattr(self._service_sub, "cancel", None)
            try:
                if callable(cancel):
                    cancel()
            except Exception:  # noqa: BLE001
                pass
        self._service_sub = None
        if self._service_started and self._service is not None:
            stopper = getattr(self._service, "stop", None)
            if callable(stopper):
                try:
                    stopper()
                except Exception:  # noqa: BLE001
                    log.exception("输入服务停止失败")
            self._service_started = False
        if self._service_signal is not None:
            try:
                self._service_signal.disconnect(self._on_service_event)
            except Exception:  # noqa: BLE001
                pass
        self._service_signal = None
        if self._service_raw is not None:
            try:
                self._service_raw.disconnect(self._on_service_raw)
            except Exception:  # noqa: BLE001
                pass
        self._service_raw = None
        self._raw_available = False
        self._active = False
        self._down.clear()
        self._digit_down.clear()
        self._held_digits.clear()

    def feed(self, token: str) -> None:
        """测试 / 外部输入：直接注入一个 token（60ms 内相同 token 去重）。"""
        text = normalize_token(token)
        if not text:
            return
        now = time.monotonic()
        last = self._recent.get(text, 0.0)
        if (now - last) * 1000.0 < self._dedupe_ms:
            return
        self._recent[text] = now
        if len(self._recent) > 32:
            self._recent = {key: value for key, value in self._recent.items() if now - value < 5.0}
        self.token.emit(text)

    # -- 接入 Lead 的输入服务 -----------------------------------------------
    def _service_object(self):
        if self.ctx is not None:
            services = getattr(self.ctx, "services", None)
            service = getattr(services, "input", None) if services is not None else None
            if service is not None:
                return service
        try:
            import endbox.core.input as core_input  # noqa: PLC0415
            from endbox.core.input import service as core_service  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            return None
        getter = getattr(core_service, "get_service", None)
        if callable(getter):
            try:
                instance = getter()
            except Exception:  # noqa: BLE001
                instance = None
            if instance is not None:
                return instance
        for factory_name in ("get_service", "create_service", "make_service"):
            factory = getattr(core_input, factory_name, None)
            if callable(factory):
                try:
                    instance = factory()
                except Exception:  # noqa: BLE001
                    continue
                if instance is not None:
                    return instance
        for name in ("input_service", "input"):
            candidate = getattr(core_input, name, None)
            if candidate is not None and not hasattr(candidate, "__path__"):
                return candidate
        return None

    def _try_service_backend(self) -> bool:
        service = self._service_object()
        if service is None:
            return False
        starter = getattr(service, "start", None)
        if callable(starter):
            try:
                if starter() is False:
                    return False
                self._service_started = True
            except Exception:  # noqa: BLE001
                return False
        for method_name in ("subscribe", "add_listener", "register_listener", "on_key", "add_handler"):
            method = getattr(service, method_name, None)
            if not callable(method):
                continue
            try:
                subscription = method(self._on_service_event)
            except TypeError:
                continue
            except Exception:  # noqa: BLE001
                log.exception("输入服务订阅失败（%s）", method_name)
                continue
            self._service = service
            self._service_sub = subscription
            self._try_raw(service)
            return True
        # 有些实现直接暴露 Qt signal 而不是订阅方法
        for signal_name in ("token", "key_pressed", "key", "pressed"):
            signal = getattr(service, signal_name, None)
            connect = getattr(signal, "connect", None)
            if callable(connect):
                try:
                    connect(self._on_service_event)
                except Exception:  # noqa: BLE001
                    continue
                self._service = service
                self._service_signal = signal
                self._try_raw(service)
                return True
        return False

    @staticmethod
    def _extract_token(payload) -> str:
        if payload is None:
            return ""
        if isinstance(payload, str):
            return payload
        if isinstance(payload, dict):
            for key in ("token", "combo", "key"):
                value = payload.get(key)
                if value:
                    return str(value)
            return ""
        token = getattr(payload, "token", None)
        if token:
            return str(token)
        return ""

    def _try_raw(self, service) -> bool:
        """接输入服务的 raw 信号：数字键的短按/长按要靠「松开」事件判定。"""
        raw = getattr(service, "raw", None)
        connect = getattr(raw, "connect", None)
        if not callable(connect):
            return False
        try:
            connect(self._on_service_raw)
        except Exception:  # noqa: BLE001
            return False
        self._service_raw = raw
        self._raw_available = True
        return True

    def _on_service_event(self, *args, **kwargs) -> None:
        payload = kwargs.get("token") or kwargs.get("key") or kwargs.get("combo") or kwargs.get("event")
        if payload is None:
            for item in args:
                token = self._extract_token(item)
                if token:
                    self._handle_token(token)
                    return
            return
        if isinstance(payload, dict):
            payload = payload.get("token") or payload.get("key") or payload.get("combo")
        token = self._extract_token(payload)
        if token:
            self._handle_token(token)

    def _handle_token(self, token: str) -> None:
        text = normalize_token(token)
        if not text:
            return
        if text in ("1", "2", "3", "4"):
            # 按下瞬间不知道是短按还是长按：有 raw 通道时等松开再判定；
            # 没有 raw 时退化为「按数字即战技」。
            if self._raw_available:
                return
            self.feed(text)
            return
        if text.startswith("HOLD+"):
            try:
                self._held_digits.add(int(text.rsplit("+", 1)[-1]))
            except ValueError:
                pass
        self.feed(text)
    def _on_service_raw(self, event) -> None:
        """KeyEvent（down/up）：短按数字 → 战技 token；长按由 HOLD 信号负责。

        连携键（默认 E）在输入层是"修饰键"，不会产生 token，所以要看 raw 的 vk：
        E 按下（down）时直接转发一次「E」，供运行态匹配连携步骤。
        切换干员键（默认 F1~F4）同理从 raw 的 vk 识别（core.input 的钩子表里
        可能没有 F1~F4，适配层不依赖它，只要 raw 带 vk 就能工作）。
        """
        try:
            if getattr(event, "source", "") != "keyboard":
                return
            link_key = normalize_key(self._keys.get("link"), "E")
            kind = getattr(event, "kind", "")
            vk = int(getattr(event, "vk", 0) or 0)
            if (
                kind == "down"
                and link_key
                and vk == (vk_for(link_key) or -1)
            ):
                self.feed(link_key)
                return
            if kind == "down" and vk:
                for slot, key in self._switch_keys():
                    if key and vk == (vk_for(key) or -1):
                        self.feed(switch_key(self._keys, slot))
                        return
            token = normalize_token(getattr(event, "token", ""))
            if token not in ("1", "2", "3", "4"):
                return
            digit = int(token)
            stamp = float(getattr(event, "timestamp", 0.0) or time.monotonic())
            if kind == "down":
                self._digit_down[digit] = stamp
                return
            if kind != "up":
                return
            started = self._digit_down.pop(digit, None)
            held = digit in self._held_digits
            self._held_digits.discard(digit)
            if started is None or held:
                return
            if (stamp - started) * 1000.0 < self._hold_ms:
                self.feed(str(digit))
        except Exception:  # noqa: BLE001 - 输入事件解析失败不能崩主线程
            log.exception("raw 事件处理失败")

    # -- Windows 轮询兜底 ---------------------------------------------------
    def _pressed(self, vk: int | None) -> bool:
        if vk is None or self._user32 is None:
            return False
        try:
            return bool(self._user32.GetAsyncKeyState(int(vk)) & 0x8000)
        except Exception:  # noqa: BLE001
            return False

    def _rising(self, key: str) -> bool:
        vk = vk_for(key)
        if vk is None:
            return False
        state = self._pressed(vk)
        previous = self._down.get(vk, False)
        self._down[vk] = state
        return state and not previous

    def _poll(self) -> None:
        if not self._active or self._user32 is None:
            return
        keys = self._keys
        link = normalize_key(keys.get("link"), "E")
        now = time.monotonic()
        hold_ms = int(self._hold_ms)  # 运行设置里的长按阈值（set_runtime_options 同步）
        # 数字键：按下记时间，松开判定「短按 = 战技 / 长按 = 终结技」
        for number in range(1, 5):
            vk = ord(str(number))
            state = self._pressed(vk)
            previous = self._down.get(vk, False)
            if state and not previous:
                self._digit_down[number] = now
            elif not state and previous:
                started = self._digit_down.pop(number, None)
                if started is not None:
                    if (now - started) * 1000.0 >= hold_ms:
                        self.token.emit(f"HOLD+{number}")
                    else:
                        self.token.emit(str(number))
            self._down[vk] = state
        # 连携键（单按 = 连携）
        if self._rising(link):
            self.token.emit(link)
        # 切换干员（默认 F1~F4，可在键位设置里改）
        seen: set[str] = set()
        for _slot, key in self._switch_keys():
            if not key or key in seen:
                continue
            seen.add(key)
            if self._rising(key):
                self.token.emit(key)
        # 功能键（F8/F9/F6/F7/F10/F11 …）
        for name in ("exit", "reset", "alpha_down", "alpha_up", "loop_toggle", "loop_jump"):
            key = normalize_key(keys.get(name), "")
            if key and self._rising(key):
                self.token.emit(key)
