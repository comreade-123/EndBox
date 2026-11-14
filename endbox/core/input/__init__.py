# -*- coding: utf-8 -*-
"""EndBox 全局输入服务包（键盘低级钩子 + 手柄轮询）。

最常用的入口::

    from endbox.core.input import InputService, KeyEvent, normalize_token, TOKENS

    svc = InputService()
    svc.token.connect(handle_token)      # handle_token(token: str)
    svc.raw.connect(handle_event)        # handle_event(event: KeyEvent)
    svc.start()                          # 幂等
    svc.stop()                           # 幂等

``normalize_token`` 把 ``q1`` / ``alt 3`` / ``pad-a`` 这类写法归一化成规范 token；
``TOKENS`` / ``TOKEN_SET`` 是全部合法 token（timeline 的按对键只认这些）。

详见 ``docs/input.md``。
"""
from __future__ import annotations

from endbox.core.input.models import InputBackendError, KeyEvent
from endbox.core.input.tokens import (
    DIGITS,
    DIGIT_TOKENS,
    FUNCTION_TOKENS,
    HOLD_TOKENS,
    KEYBOARD_TOKENS,
    MODIFIERS,
    MODIFIER_PRIORITY,
    PAD_FACE_TOKENS,
    PAD_MOD_TOKENS,
    PAD_SHOULDER_TOKENS,
    PAD_TOKEN_SET,
    PAD_TOKENS,
    PLAIN_MODIFIER_TOKENS,
    TOKEN_SET,
    TOKENS,
    digit_of,
    digit_token,
    hold_token,
    is_known_token,
    modifier_for,
    normalize_token,
)
from endbox.core.input.keyboard import (
    HOLD_MS_DEFAULT,
    HOLD_MS_MAX,
    HOLD_MS_MIN,
    HoldTracker,
    KeyboardBackend,
    ModifierTracker,
    NullKeyboardBackend,
    WindowsKeyboardBackend,
    base_token_for_vk,
    digit_for_vk,
    token_for_key,
    vk_for_digit,
)
from endbox.core.input.gamepad import (
    GAMEPAD_MODES,
    GamepadBackend,
    NullGamepadBackend,
    WinMMGamepadBackend,
    XInputGamepadBackend,
    XINPUT_BUTTON_MAP,
    XINPUT_STATE,
    XINPUT_GAMEPAD,
    create_gamepad_backend,
    diff_gamepad,
    normalize_gamepad_mode,
)
from endbox.core.input.service import (
    InputService,
    Subscription,
    get_service,
    is_admin,
)

__all__ = [
    # -- 服务 --------------------------------------------------------------
    "InputService",
    "Subscription",
    "get_service",
    "is_admin",
    # -- 事件 / 错误 --------------------------------------------------------
    "KeyEvent",
    "InputBackendError",
    # -- token 常量 & 归一化（timeline 依赖，不可改名）---------------------
    "TOKENS",
    "TOKEN_SET",
    "KEYBOARD_TOKENS",
    "PAD_TOKENS",
    "PAD_TOKEN_SET",
    "DIGITS",
    "DIGIT_TOKENS",
    "MODIFIERS",
    "MODIFIER_PRIORITY",
    "FUNCTION_TOKENS",
    "PLAIN_MODIFIER_TOKENS",
    "HOLD_TOKENS",
    "PAD_FACE_TOKENS",
    "PAD_SHOULDER_TOKENS",
    "PAD_MOD_TOKENS",
    "normalize_token",
    "digit_token",
    "hold_token",
    "modifier_for",
    "digit_of",
    "is_known_token",
    # -- 键盘 --------------------------------------------------------------
    "KeyboardBackend",
    "WindowsKeyboardBackend",
    "NullKeyboardBackend",
    "HoldTracker",
    "ModifierTracker",
    "HOLD_MS_DEFAULT",
    "HOLD_MS_MIN",
    "HOLD_MS_MAX",
    "base_token_for_vk",
    "token_for_key",
    "digit_for_vk",
    "vk_for_digit",
    # -- 手柄 --------------------------------------------------------------
    "GamepadBackend",
    "NullGamepadBackend",
    "XInputGamepadBackend",
    "WinMMGamepadBackend",
    "XINPUT_GAMEPAD",
    "XINPUT_STATE",
    "XINPUT_BUTTON_MAP",
    "GAMEPAD_MODES",
    "create_gamepad_backend",
    "diff_gamepad",
    "normalize_gamepad_mode",
]
