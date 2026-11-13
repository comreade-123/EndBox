# -*- coding: utf-8 -*-
"""全局输入服务测试：**不安装真实全局钩子**，全部通过可注入的 fake backend。

覆盖：
  * token 常量表 / ``normalize_token`` 兼容写法 / 数字与修饰键辅助函数；
  * VK → token 映射（Q/E/ALT 组合优先级、H、F6–F11、未绑定键、修饰键本身）；
  * 修饰键状态机 ``ModifierTracker``；
  * 长按判定 ``HoldTracker``（阈值、无修饰限制、自动重复、松开取消、阈值夹取）；
  * XInput 结构体布局、按钮位、``diff_gamepad`` 按下/松开/组合键、DS 与 Xbox 映射；
  * ``InputService``：生命周期幂等、错误/告警信号、token/raw 信号、长按端到端、
    订阅（自动启动 / 最后一个自动订阅取消后自动停止 / 启动失败抛错）、手柄开关与轮询；
  * 与 timeline 的 token 契约一致性。
"""
from __future__ import annotations

import ctypes
import sys

import pytest

from endbox.core.input import (
    DIGITS,
    HOLD_MS_DEFAULT,
    HOLD_MS_MAX,
    HOLD_MS_MIN,
    HOLD_TOKENS,
    PAD_MOD_TOKENS,
    PAD_TOKENS,
    TOKEN_SET,
    TOKENS,
    GamepadBackend,
    HoldTracker,
    InputBackendError,
    InputService,
    KeyEvent,
    KeyboardBackend,
    ModifierTracker,
    NullKeyboardBackend,
    digit_of,
    digit_token,
    hold_token,
    is_known_token,
    modifier_for,
    normalize_token,
    token_for_key,
    vk_for_digit,
)
from endbox.core.input import gamepad as G
from endbox.core.input import keyboard as K
from endbox.core.input import service as SV


# ---------------------------------------------------------------------------
# 假后端 / 假时钟（测试绝不碰真实钩子 / 真实手柄）
# ---------------------------------------------------------------------------
class FakeClock:
    """可推进的单调时钟，让长按判定无需 sleep。"""

    def __init__(self, start: float = 1000.0):
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class FakeKeyboardBackend(KeyboardBackend):
    name = "fake"

    def __init__(self, fail: str | None = None):
        self.fail = fail
        self.started = 0
        self.stopped = 0
        self.emit = None
        self.running = False

    @property
    def available(self) -> bool:
        return True

    def start(self, emit) -> None:
        if self.fail:
            raise InputBackendError(self.fail)
        self.emit = emit
        self.running = True
        self.started += 1

    def stop(self) -> None:
        self.running = False
        self.emit = None
        self.stopped += 1

    # -- 测试辅助 -----------------------------------------------------------
    def press(self, vk: int, modifiers=(), *, token: str | None = None) -> None:
        assert self.emit is not None, "backend 未启动"
        self.emit(
            KeyEvent(
                token=token if token is not None else K.token_for_key(vk, modifiers),
                kind="down",
                modifiers=tuple(modifiers),
                vk=vk,
                source="keyboard",
            )
        )

    def release(self, vk: int, modifiers=()) -> None:
        assert self.emit is not None, "backend 未启动"
        self.emit(
            KeyEvent(
                token=K.token_for_key(vk, modifiers),
                kind="up",
                modifiers=tuple(modifiers),
                vk=vk,
                source="keyboard",
            )
        )


class FakeGamepadBackend(GamepadBackend):
    name = "fake-pad"

    def __init__(self, mode: str = "xbox", events=()):
        self.mode = mode
        self._events = list(events)
        self.closed = 0
        self.polls = 0

    @property
    def available(self) -> bool:
        return True

    @property
    def connected(self) -> bool:
        return True

    def push(self, event: KeyEvent) -> None:
        self._events.append(event)

    def poll(self) -> list[KeyEvent]:
        self.polls += 1
        events, self._events = self._events, []
        return events

    def close(self) -> None:
        self.closed += 1


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def backend() -> FakeKeyboardBackend:
    return FakeKeyboardBackend()


@pytest.fixture
def service(qapp, backend, clock):
    svc = InputService(
        keyboard_backend=backend,
        clock=clock,
        admin_check=lambda: True,
    )
    yield svc
    svc.stop()


def _collect(svc: InputService):
    tokens: list[str] = []
    events: list[KeyEvent] = []
    svc.token.connect(tokens.append)
    svc.raw.connect(events.append)
    return tokens, events


# ---------------------------------------------------------------------------
# token 规范
# ---------------------------------------------------------------------------
def test_token_constants_are_unique_and_cover_contract():
    assert len(TOKENS) == len(TOKEN_SET)
    for token in ("1", "2", "3", "4", "Q+1", "Q+4", "E+1", "E+4", "ALT+1", "ALT+4"):
        assert token in TOKEN_SET
    assert set(HOLD_TOKENS) == {"HOLD+1", "HOLD+2", "HOLD+3", "HOLD+4"}
    for token in ("H", "F6", "F7", "F8", "F9", "F10", "F11"):
        assert token in TOKEN_SET
    for token in ("PAD_A", "PAD_B", "PAD_X", "PAD_Y", "PAD_LB", "PAD_RB"):
        assert token in TOKEN_SET
    assert set(PAD_MOD_TOKENS) == {
        "PAD_MOD+PAD_A",
        "PAD_MOD+PAD_B",
        "PAD_MOD+PAD_X",
        "PAD_MOD+PAD_Y",
    }
    assert set(PAD_TOKENS) <= TOKEN_SET


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("q1", "Q+1"),
        ("q+1", "Q+1"),
        ("Q 1", "Q+1"),
        ("Q+1", "Q+1"),
        ("alt3", "ALT+3"),
        ("ALT+4", "ALT+4"),
        ("e2", "E+2"),
        ("hold1", "HOLD+1"),
        ("hold+2", "HOLD+2"),
        ("h", "H"),
        ("f6", "F6"),
        ("f11", "F11"),
        ("pad-a", "PAD_A"),
        ("pada", "PAD_A"),
        ("padmod+a", "PAD_MOD+PAD_A"),
        ("PAD_MOD + PAD_A", "PAD_MOD+PAD_A"),
        ("", ""),
        (None, ""),
        ("   ", ""),
        ("5", ""),
        ("Q+5", ""),
        ("garbage", ""),
        (42, ""),
    ],
)
def test_normalize_token_variants(raw, expected):
    assert normalize_token(raw) == expected


def test_token_helpers():
    assert digit_token(1) == "1"
    assert digit_token(3, "q") == "Q+3"
    assert digit_token(4, "ALT") == "ALT+4"
    with pytest.raises(ValueError):
        digit_token(5)
    with pytest.raises(ValueError):
        digit_token(1, "CTRL")
    assert hold_token(2) == "HOLD+2"
    with pytest.raises(ValueError):
        hold_token(0)
    assert modifier_for("Q+2") == "Q"
    assert modifier_for("ALT+4") == "ALT"
    assert modifier_for("hold+1") == ""
    assert digit_of("Q+2") == "2"
    assert digit_of("HOLD+4") == "4"
    assert digit_of("3") == "3"
    assert digit_of("nope") == ""
    assert is_known_token("q+1") is True
    assert is_known_token("PAD_MOD+PAD_Y") is True
    assert is_known_token("hello") is False


def test_all_digit_slots_covered():
    assert DIGITS == ("1", "2", "3", "4")
    for digit in DIGITS:
        assert digit in TOKEN_SET
        assert f"HOLD+{digit}" in TOKEN_SET


def test_tokens_agree_with_timeline_normalizer():
    """timeline.runtime.normalize_token 必须原样接受输入服务产出的 token。"""
    from endbox.modules.timeline.runtime import normalize_token as timeline_normalize

    for token in (
        "1", "2", "3", "4",
        "Q+1", "Q+2", "Q+3", "Q+4",
        "E+1", "E+2", "E+3", "E+4",
        "ALT+1", "ALT+2", "ALT+3", "ALT+4",
        "HOLD+1", "HOLD+4", "H", "F6", "F7", "F8", "F9", "F10", "F11",
    ):
        assert timeline_normalize(token) == token


# ---------------------------------------------------------------------------
# VK 映射 / 修饰键
# ---------------------------------------------------------------------------
def test_vk_mapping():
    assert K.base_token_for_vk(K.VK_1) == "1"
    assert K.base_token_for_vk(K.VK_4) == "4"
    assert K.base_token_for_vk(K.VK_Q) == "Q"
    assert K.base_token_for_vk(K.VK_E) == "E"
    assert K.base_token_for_vk(K.VK_ALT) == "ALT"
    assert K.base_token_for_vk(K.VK_LMENU) == "ALT"
    assert K.base_token_for_vk(K.VK_RMENU) == "ALT"
    assert K.base_token_for_vk(K.VK_H) == "H"
    assert K.base_token_for_vk(K.VK_F6) == "F6"
    assert K.base_token_for_vk(K.VK_F11) == "F11"
    assert K.base_token_for_vk(0x41) == ""  # A 未绑定
    for digit in DIGITS:
        assert K.digit_for_vk(vk_for_digit(digit)) == digit
    assert K.digit_for_vk(0x41) == ""
    with pytest.raises(ValueError):
        vk_for_digit(5)


def test_token_for_key_combinations():
    # 无修饰单按数字
    assert token_for_key(K.VK_1, ()) == "1"
    assert token_for_key(K.VK_2, []) == "2"
    # 修饰键组合
    assert token_for_key(K.VK_1, ("Q",)) == "Q+1"
    assert token_for_key(K.VK_2, ("E",)) == "E+2"
    assert token_for_key(K.VK_3, ("ALT",)) == "ALT+3"
    # 组合优先级 ALT > Q > E
    assert token_for_key(K.VK_4, ("Q", "ALT", "E")) == "ALT+4"
    assert token_for_key(K.VK_4, ("E", "Q")) == "Q+4"
    # 功能键 / 重击键
    assert token_for_key(K.VK_H, ("Q",)) == "H"
    for name, vk in (("F6", K.VK_F6), ("F7", K.VK_F7), ("F8", K.VK_F8),
                     ("F9", K.VK_F9), ("F10", K.VK_F10), ("F11", K.VK_F11)):
        assert token_for_key(vk, ()) == name
    # 修饰键本身 / 未绑定键不产生动作 token
    assert token_for_key(K.VK_Q, ("Q",)) == ""
    assert token_for_key(K.VK_ALT, ("ALT",)) == ""
    assert token_for_key(0x41, ()) == ""


def test_modifier_tracker_state_machine():
    tracker = ModifierTracker()
    assert tracker.modifiers() == ()
    assert tracker.update(K.VK_ALT, True) == ("ALT",)
    assert tracker.update(K.VK_Q, True) == ("ALT", "Q")
    assert tracker.update(K.VK_E, True) == ("ALT", "Q", "E")
    assert tracker.held == frozenset({"ALT", "Q", "E"})
    assert tracker.update(K.VK_Q, False) == ("ALT", "E")
    assert tracker.update(K.VK_LMENU, False) == ("E",)  # 左右 Alt 都算 ALT
    assert tracker.update(K.VK_E, False) == ()
    tracker.update(K.VK_Q, True)
    tracker.reset()
    assert tracker.held == frozenset()


# ---------------------------------------------------------------------------
# 长按判定
# ---------------------------------------------------------------------------
def test_hold_tracker_threshold_and_single_fire(clock):
    tracker = HoldTracker(280, clock=clock)
    tracker.press("1", ())
    clock.advance(0.279)
    assert tracker.due() == []
    clock.advance(0.002)
    assert tracker.due() == ["1"]
    clock.advance(1.0)
    assert tracker.due() == []  # 只报一次
    tracker.release("1")
    assert tracker.pending == {}


def test_hold_tracker_requires_no_modifier(clock):
    tracker = HoldTracker(280, clock=clock)
    tracker.press("2", ("Q",))
    clock.advance(5.0)
    assert tracker.due() == []
    tracker.reset()
    tracker.press("2", ("ALT", "Q"))
    clock.advance(5.0)
    assert tracker.due() == []


def test_hold_tracker_ignores_autorepeat(clock):
    tracker = HoldTracker(280, clock=clock)
    tracker.press("3", ())
    clock.advance(0.2)
    tracker.press("3", ())  # 自动重复：保留第一次按下的时间
    clock.advance(0.09)
    assert tracker.due() == ["3"]


def test_hold_tracker_release_cancels(clock):
    tracker = HoldTracker(280, clock=clock)
    tracker.press("4", ())
    clock.advance(0.1)
    tracker.release("4")
    clock.advance(1.0)
    assert tracker.due() == []


def test_hold_tracker_feed_key_events(clock):
    tracker = HoldTracker(280, clock=clock)
    tracker.feed(KeyEvent(token="3", kind="down", vk=K.VK_3, source="keyboard"))
    clock.advance(0.3)
    assert tracker.due() == ["3"]
    tracker.feed(KeyEvent(token="3", kind="up", vk=K.VK_3, source="keyboard"))
    assert tracker.pending == {}
    # 非数字 / 非键盘事件被忽略
    tracker.feed(KeyEvent(token="H", kind="down", vk=K.VK_H, source="keyboard"))
    tracker.feed(KeyEvent(token="PAD_A", kind="down", vk=0x1000, source="gamepad"))
    assert tracker.pending == {}


def test_hold_tracker_clamps_threshold():
    assert HoldTracker(1).hold_ms == HOLD_MS_MIN
    assert HoldTracker(99999).hold_ms == HOLD_MS_MAX
    tracker = HoldTracker()
    assert tracker.hold_ms == HOLD_MS_DEFAULT
    assert tracker.set_hold_ms(350) == 350
    assert tracker.set_hold_ms(0) == HOLD_MS_MIN
    assert tracker.set_hold_ms("bad") == HOLD_MS_DEFAULT


# ---------------------------------------------------------------------------
# XInput 结构 / 手柄映射
# ---------------------------------------------------------------------------
def test_xinput_struct_layout():
    size = ctypes.sizeof(G.XINPUT_GAMEPAD)
    assert size == 12  # WORD + BYTE + BYTE + 4×SHORT
    assert G.XINPUT_GAMEPAD.wButtons.offset == 0
    assert G.XINPUT_GAMEPAD.bLeftTrigger.offset == 2
    assert G.XINPUT_GAMEPAD.bRightTrigger.offset == 3
    assert G.XINPUT_GAMEPAD.sThumbLX.offset == 4
    assert G.XINPUT_GAMEPAD.sThumbLY.offset == 6
    assert G.XINPUT_GAMEPAD.sThumbRX.offset == 8
    assert G.XINPUT_GAMEPAD.sThumbRY.offset == 10
    if sys.platform.startswith("win"):
        assert ctypes.sizeof(G.XINPUT_STATE) == 16
        assert G.XINPUT_STATE.dwPacketNumber.offset == 0
        assert G.XINPUT_STATE.Gamepad.offset == 4


def test_xinput_button_bits():
    assert G.XINPUT_A == 0x1000
    assert G.XINPUT_B == 0x2000
    assert G.XINPUT_X == 0x4000
    assert G.XINPUT_Y == 0x8000
    assert G.XINPUT_LB == 0x0100
    assert G.XINPUT_RB == 0x0200
    assert G.XINPUT_BUTTON_MAP[G.XINPUT_A] == "PAD_A"
    assert G.XINPUT_BUTTON_MAP[G.XINPUT_Y] == "PAD_Y"
    assert G.XINPUT_BUTTON_MAP[G.XINPUT_RB] == "PAD_RB"


def test_diff_gamepad_press_release_and_modifier():
    mapping = G.XINPUT_BUTTON_MAP
    # 按下 A
    events = G.diff_gamepad(0, G.XINPUT_A, mapping, modifier_bit=G.XINPUT_LB)
    assert [(e.token, e.kind) for e in events] == [("PAD_A", "down")]
    # 按住 A 再按 LB：只报 LB 按下
    events = G.diff_gamepad(G.XINPUT_A, G.XINPUT_A | G.XINPUT_LB, mapping, modifier_bit=G.XINPUT_LB)
    assert [(e.token, e.kind) for e in events] == [("PAD_LB", "down")]
    # LB + A = 组合键
    events = G.diff_gamepad(G.XINPUT_LB, G.XINPUT_LB | G.XINPUT_A, mapping, modifier_bit=G.XINPUT_LB)
    assert [(e.token, e.kind) for e in events] == [("PAD_MOD+PAD_A", "down")]
    assert events[0].modifiers == ("PAD_MOD",)
    # 松开 A：up 事件带基础 token
    events = G.diff_gamepad(G.XINPUT_LB | G.XINPUT_A, G.XINPUT_LB, mapping, modifier_bit=G.XINPUT_LB)
    assert [(e.token, e.kind) for e in events] == [("PAD_A", "up")]
    # 无变化：没有事件
    assert G.diff_gamepad(0, 0, mapping, modifier_bit=G.XINPUT_LB) == []


def test_gamepad_mode_and_factory():
    assert G.GAMEPAD_MODES == ("off", "xbox", "ds")
    assert G.normalize_gamepad_mode("XBOX") == "xbox"
    assert G.normalize_gamepad_mode("") == "off"
    assert G.normalize_gamepad_mode(None) == "off"
    with pytest.raises(ValueError):
        G.normalize_gamepad_mode("switch")
    assert isinstance(G.create_gamepad_backend("off"), G.NullGamepadBackend)


def test_winmm_layout_and_mappings():
    # joyGetPosEx 的 JOYINFOEX 是 13 个 DWORD
    assert ctypes.sizeof(G.JOYINFOEX) == 13 * 4
    assert G.JOYINFOEX.dwSize.offset == 0
    assert G.JOYINFOEX.dwButtons.offset == 8 * 4
    # DS 风格：B1=□(PAD_X) / B2=×(PAD_A) / B5=L1(PAD_LB)
    assert G.WINMM_DS_MAP[0x0001] == "PAD_X"
    assert G.WINMM_DS_MAP[0x0002] == "PAD_A"
    assert G.WINMM_DS_MAP[0x0010] == "PAD_LB"
    # Xbox 风格：B1=A / B5=LB
    assert G.WINMM_XBOX_MAP[0x0001] == "PAD_A"
    assert G.WINMM_XBOX_MAP[0x0010] == "PAD_LB"


# ---------------------------------------------------------------------------
# InputService 生命周期
# ---------------------------------------------------------------------------
def test_service_initial_state(service, backend):
    assert service.enabled is False
    assert service.running is False
    assert service.hold_ms == HOLD_MS_DEFAULT
    assert service.gamepad_mode == "off"
    assert service.last_error is None
    assert service.listener_count == 0
    assert backend.started == 0  # 构造服务绝不装钩子
    status = service.status()
    assert status["enabled"] is False
    assert status["gamepad_mode"] == "off"
    assert status["keyboard"] == "fake"


def test_start_stop_are_idempotent(service, backend):
    assert service.start() is True
    assert service.enabled is True
    assert backend.started == 1
    # 重复 start：不重复装钩子
    assert service.start() is True
    assert service.start() is True
    assert backend.started == 1
    service.stop()
    assert service.enabled is False
    assert backend.stopped == 1
    service.stop()
    service.stop()
    assert backend.stopped == 1
    # stop 后可以重新 start
    assert service.start() is True
    assert backend.started == 2
    service.stop()
    assert backend.stopped == 2


def test_start_failure_emits_error_and_degrades(qapp, clock):
    backend = FakeKeyboardBackend(fail="模拟 SetWindowsHookEx 失败（错误码 5）")
    svc = InputService(keyboard_backend=backend, clock=clock, admin_check=lambda: True)
    errors: list[str] = []
    warnings: list[str] = []
    svc.error.connect(errors.append)
    svc.warning.connect(warnings.append)
    try:
        assert svc.start() is False
        assert svc.enabled is False
        assert svc.last_error and "错误码 5" in svc.last_error
        assert errors and errors[0] == svc.last_error
    finally:
        svc.stop()


def test_non_admin_emits_warning(backend, clock, qapp):
    svc = InputService(keyboard_backend=backend, clock=clock, admin_check=lambda: False)
    warnings: list[str] = []
    svc.warning.connect(warnings.append)
    try:
        assert svc.start() is True
        assert svc.enabled is True
        assert any("管理员" in text and "UIPI" in text for text in warnings)
        assert svc.is_admin() is False
    finally:
        svc.stop()


def test_non_windows_null_backend_degrades(qapp, clock):
    svc = InputService(
        keyboard_backend=NullKeyboardBackend(),
        clock=clock,
        admin_check=lambda: True,
    )
    errors: list[str] = []
    svc.error.connect(errors.append)
    try:
        assert svc.start() is False
        assert svc.enabled is False
        assert errors and "仅 Windows" in errors[0]
    finally:
        svc.stop()


# ---------------------------------------------------------------------------
# 信号 / token 发布
# ---------------------------------------------------------------------------
def test_token_and_raw_signals(service, backend):
    tokens, events = _collect(service)
    service.start()
    backend.press(K.VK_Q)                      # 修饰键本身：只进 raw，不发 token
    backend.press(K.VK_1, ("Q",))              # Q+1
    backend.press(K.VK_3, ("ALT", "Q"))        # ALT+3（优先级）
    backend.press(K.VK_H)                      # H
    backend.press(K.VK_F8)                     # F8
    backend.press(0x41)                        # 未绑定键
    backend.release(K.VK_1, ("Q",))
    assert tokens == ["Q+1", "ALT+3", "H", "F8"]
    assert len(events) == 7
    assert [event.kind for event in events] == ["down"] * 6 + ["up"]
    assert events[0].token == ""               # 修饰键不带动作 token
    assert events[1].token == "Q+1" and events[1].modifiers == ("Q",)


def test_plain_digit_token_then_hold(service, backend, clock):
    tokens, events = _collect(service)
    service.start()
    backend.press(K.VK_1)                      # 无修饰 → "1"
    assert tokens == ["1"]
    clock.advance(0.2)
    service._check_holds()  # noqa: SLF001 - 白盒驱动长按定时器
    assert tokens == ["1"]
    clock.advance(0.1)
    service._check_holds()  # noqa: SLF001
    assert tokens == ["1", "HOLD+1"]
    hold_events = [event for event in events if event.kind == "hold"]
    assert len(hold_events) == 1
    assert hold_events[0].token == "HOLD+1"
    assert hold_events[0].vk == vk_for_digit("1")
    # 继续按住不重复发
    clock.advance(1.0)
    service._check_holds()  # noqa: SLF001
    assert tokens == ["1", "HOLD+1"]
    # 松开后重新按，可以再次触发
    backend.release(K.VK_1)
    backend.press(K.VK_1)
    clock.advance(0.4)
    service._check_holds()  # noqa: SLF001
    assert tokens == ["1", "HOLD+1", "1", "HOLD+1"]


def test_hold_not_emitted_for_modifier_combo(service, backend, clock):
    tokens, _ = _collect(service)
    service.start()
    backend.press(K.VK_Q)
    backend.press(K.VK_2, ("Q",))
    clock.advance(2.0)
    service._check_holds()  # noqa: SLF001
    assert tokens == ["Q+2"]


def test_hold_cancelled_when_released_early(service, backend, clock):
    tokens, _ = _collect(service)
    service.start()
    backend.press(K.VK_4)
    clock.advance(0.1)
    backend.release(K.VK_4)
    clock.advance(1.0)
    service._check_holds()  # noqa: SLF001
    assert tokens == ["4"]


def test_set_hold_ms(service, clock):
    assert service.set_hold_ms(10) == HOLD_MS_MIN
    assert service.set_hold_ms(9999) == HOLD_MS_MAX
    assert service.set_hold_ms(350) == 350
    assert service.hold_ms == 350
    assert service.set_hold_ms("bad") == HOLD_MS_DEFAULT


def test_stop_clears_pending_hold(service, backend, clock):
    tokens, _ = _collect(service)
    service.start()
    backend.press(K.VK_1)
    assert tokens == ["1"]
    service.stop()
    clock.advance(1.0)
    # 定时器已停；即使手工调用也不应再发（tracker 已 reset）
    service._check_holds()  # noqa: SLF001
    assert tokens == ["1"]
    assert service._hold_tracker.pending == {}  # noqa: SLF001


# ---------------------------------------------------------------------------
# 订阅（timeline.input_adapter 的接入点）
# ---------------------------------------------------------------------------
def test_subscribe_auto_starts_and_auto_stops(service, backend):
    received: list[str] = []
    assert service.enabled is False
    sub = service.subscribe(received.append)
    assert service.enabled is True
    assert backend.started == 1
    assert sub.active is True
    assert service.listener_count == 1
    backend.press(K.VK_1, ("Q",))
    assert received == ["Q+1"]
    sub.cancel()
    assert sub.active is False
    assert service.listener_count == 0
    assert service.enabled is False  # 最后一个“自动启动”的订阅取消 → 自动停止
    assert backend.stopped == 1
    sub.cancel()  # 幂等
    assert backend.stopped == 1


def test_manual_start_keeps_running_after_unsubscribe(service, backend):
    assert service.start() is True
    received: list[str] = []
    sub = service.subscribe(received.append)
    sub.cancel()
    assert service.enabled is True
    assert service.listener_count == 0
    service.stop()
    assert service.enabled is False


def test_subscribe_failure_raises_and_rolls_back(qapp, clock):
    backend = FakeKeyboardBackend(fail="模拟安装钩子失败")
    svc = InputService(keyboard_backend=backend, clock=clock, admin_check=lambda: True)
    try:
        with pytest.raises(InputBackendError):
            svc.subscribe(lambda token: None)
        assert svc.enabled is False
        assert svc.listener_count == 0
        assert svc.last_error
    finally:
        svc.stop()


def test_subscribe_callback_exception_is_isolated(service, backend):
    calls: list[str] = []

    def boom(token: str) -> None:
        calls.append(token)
        raise RuntimeError("订阅者内部异常")

    ok: list[str] = []
    service.subscribe(boom)
    service.subscribe(ok.append)
    service.start()
    backend.press(K.VK_H)
    assert calls == ["H"]
    assert ok == ["H"]  # 第一个订阅者炸了，第二个照常收到


# ---------------------------------------------------------------------------
# 手柄
# ---------------------------------------------------------------------------
def test_gamepad_off_by_default(service):
    assert service.gamepad_mode == "off"
    assert service._gamepad_timer.isActive() is False  # noqa: SLF001


def test_set_gamepad_switches_and_polls(qapp, backend, clock):
    pads: dict[str, FakeGamepadBackend] = {}

    def factory(mode: str) -> FakeGamepadBackend:
        pad = FakeGamepadBackend(mode)
        pads[mode] = pad
        return pad

    svc = InputService(
        keyboard_backend=backend,
        gamepad_factory=factory,
        clock=clock,
        admin_check=lambda: True,
    )
    tokens, events = _collect(svc)
    try:
        assert svc.start() is True
        assert svc.gamepad_mode == "off"
        assert svc.set_gamepad("xbox") == "xbox"
        assert svc.gamepad_mode == "xbox"
        assert svc._gamepad_timer.isActive() is True  # noqa: SLF001
        pad = pads["xbox"]
        pad.push(KeyEvent(token="PAD_A", kind="down", vk=G.XINPUT_A, source="gamepad"))
        pad.push(
            KeyEvent(
                token="PAD_MOD+PAD_B",
                kind="down",
                modifiers=("PAD_MOD",),
                vk=G.XINPUT_B,
                source="gamepad",
            )
        )
        svc._poll_gamepad()  # noqa: SLF001 - 白盒触发一次轮询
        assert tokens == ["PAD_A", "PAD_MOD+PAD_B"]
        assert [event.source for event in events] == ["gamepad", "gamepad"]
        # 同一模式重复设置不会重建后端
        assert svc.set_gamepad("xbox") == "xbox"
        assert pads["xbox"] is pad
        # 关闭
        assert svc.set_gamepad("off") == "off"
        assert svc._gamepad_timer.isActive() is False  # noqa: SLF001
        assert pad.closed == 1
        with pytest.raises(ValueError):
            svc.set_gamepad("switch")
    finally:
        svc.stop()


def test_unavailable_gamepad_emits_warning(backend, clock, qapp):
    warnings: list[str] = []
    svc = InputService(
        keyboard_backend=backend,
        gamepad_factory=lambda mode: G.NullGamepadBackend(mode),
        clock=clock,
        admin_check=lambda: True,
        gamepad="xbox",
    )
    svc.warning.connect(warnings.append)
    try:
        assert svc.start() is True  # 键盘可用，手柄降级不影响
        assert svc.enabled is True
        assert any("手柄不可用" in text for text in warnings)
        assert svc.gamepad_mode == "xbox"
        assert svc._gamepad_timer.isActive() is False  # noqa: SLF001
    finally:
        svc.stop()


def test_gamepad_factory_exception_is_contained(backend, clock, qapp):
    def broken_factory(mode: str):
        raise OSError("xinput1_4.dll 缺失")

    svc = InputService(
        keyboard_backend=backend,
        gamepad_factory=broken_factory,
        clock=clock,
        admin_check=lambda: True,
        gamepad="ds",
    )
    warnings: list[str] = []
    svc.warning.connect(warnings.append)
    try:
        assert svc.start() is True
        assert any("手柄初始化失败" in text for text in warnings)
    finally:
        svc.stop()


# ---------------------------------------------------------------------------
# 管理员检测 / 单例
# ---------------------------------------------------------------------------
def test_is_admin_returns_bool():
    assert isinstance(SV.is_admin(), bool)


def test_default_keyboard_backend_selection(qapp):
    svc = InputService(admin_check=lambda: True)
    try:
        backend = svc._keyboard_backend  # noqa: SLF001
        assert backend is None  # 懒创建：构造时不加载 DLL / 不装钩子
        assert svc.status()["keyboard"] == "none"
    finally:
        svc.stop()


def test_get_service_singleton():
    first = SV.get_service()
    second = SV.get_service()
    assert first is second
    assert isinstance(first, InputService)
    assert first.enabled is False  # 单例只是创建，不自动装钩子


# ---------------------------------------------------------------------------
# 与 timeline 采集适配层的接口兼容（不装真实钩子：用假 service 顶替单例）
# ---------------------------------------------------------------------------
def test_timeline_adapter_integrates_with_service(qapp, monkeypatch):
    from endbox.modules.timeline import input_adapter as IA

    received: list[str] = []

    class FakeService:
        def __init__(self):
            self.callback = None
            self.cancelled = 0

        def subscribe(self, callback):
            self.callback = callback
            service = self

            class _Sub:
                def cancel(inner):
                    service.cancelled += 1

            return _Sub()

    fake = FakeService()
    # input_adapter 会把 endbox.core.input.service 子模块 duck-type 成 service 对象，
    # 该模块的 subscribe() 等价于 get_service().subscribe()。
    monkeypatch.setattr(SV, "get_service", lambda: fake)

    adapter = IA.InputAdapter(backend="auto")
    adapter.token.connect(received.append)
    try:
        assert adapter.start() is True
        assert adapter.backend == "service"
        assert adapter.available is True
        assert fake.callback is not None
        # 服务订阅回调收到的是已规范化 token；适配层再归一化一次不应丢
        fake.callback("Q+1")
        fake.callback("HOLD+4")
        fake.callback("PAD_A")  # 手柄 token 原样透传（timeline 自行映射）
        assert received == ["Q+1", "HOLD+4", "PAD_A"]
    finally:
        adapter.stop()
    assert fake.cancelled == 1

