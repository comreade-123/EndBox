# -*- coding: utf-8 -*-
"""手柄输入：XInput 轮询（主）+ winmm joyGetPosEx（DS / 兜底）。

默认关闭；``InputService.set_gamepad("xbox"|"ds")`` 才启动。轮询在 Qt 主线程用
20ms 定时器完成（XInputGetState 是极轻量的本地调用，不会阻塞 UI）。

token 约定：
    PAD_A / PAD_B / PAD_X / PAD_Y     面键
    PAD_LB / PAD_RB                   肩键
    PAD_MOD+PAD_A/B/X/Y               左肩键按住 + 面键（组合键）
DS 风格走 winmm 时按 DirectInput 常见编号映射：B1=□/B2=×/B3=○/B4=△/
B5=L1/B6=R1；Xbox 风格 winmm 兜底时 B1=A…B4=Y、B5=LB、B6=RB。
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from typing import Callable

from endbox.core.log import get_logger
from endbox.core.input.models import KeyEvent

log = get_logger("core.input.gamepad")

_IS_WINDOWS = sys.platform.startswith("win")

GAMEPAD_MODES: tuple[str, ...] = ("off", "xbox", "ds")

# XInput 按钮位
XINPUT_A = 0x1000
XINPUT_B = 0x2000
XINPUT_X = 0x4000
XINPUT_Y = 0x8000
XINPUT_LB = 0x0100
XINPUT_RB = 0x0200

XINPUT_BUTTON_MAP: dict[int, str] = {
    XINPUT_A: "PAD_A",
    XINPUT_B: "PAD_B",
    XINPUT_X: "PAD_X",
    XINPUT_Y: "PAD_Y",
    XINPUT_LB: "PAD_LB",
    XINPUT_RB: "PAD_RB",
}

# winmm joyGetPosEx 的按钮位（dwButtons 的 bit0 起）
WINMM_XBOX_MAP: dict[int, str] = {
    0x0001: "PAD_A",
    0x0002: "PAD_B",
    0x0004: "PAD_X",
    0x0008: "PAD_Y",
    0x0010: "PAD_LB",
    0x0020: "PAD_RB",
}
WINMM_DS_MAP: dict[int, str] = {
    0x0001: "PAD_X",   # □
    0x0002: "PAD_A",   # ×
    0x0004: "PAD_B",   # ○
    0x0008: "PAD_Y",   # △
    0x0010: "PAD_LB",  # L1
    0x0020: "PAD_RB",  # R1
}

FACE_TOKENS = frozenset({"PAD_A", "PAD_B", "PAD_X", "PAD_Y"})
PAD_MOD = "PAD_MOD"


def normalize_gamepad_mode(mode: object) -> str:
    value = str(mode or "").strip().lower()
    if value in ("", "none", "null"):
        return "off"
    if value in GAMEPAD_MODES:
        return value
    raise ValueError(f"手柄模式必须是 {'/'.join(GAMEPAD_MODES)}，收到 {mode!r}")


def diff_gamepad(
    previous: int,
    current: int,
    mapping: dict[int, str],
    *,
    modifier_bit: int,
    now: float | None = None,
) -> list[KeyEvent]:
    """比较两次按钮位，产生 down / up KeyEvent（纯函数，供测试）。"""
    stamp = time.monotonic() if now is None else float(now)
    events: list[KeyEvent] = []
    modifier_held = bool(current & modifier_bit)
    for bit in sorted(mapping):
        token = mapping[bit]
        was = bool(previous & bit)
        is_now = bool(current & bit)
        if is_now and not was:
            if modifier_held and token in FACE_TOKENS:
                events.append(
                    KeyEvent(
                        token=f"{PAD_MOD}+{token}",
                        kind="down",
                        modifiers=(PAD_MOD,),
                        vk=bit,
                        timestamp=stamp,
                        source="gamepad",
                    )
                )
            else:
                events.append(
                    KeyEvent(
                        token=token,
                        kind="down",
                        modifiers=(PAD_MOD,) if modifier_held else (),
                        vk=bit,
                        timestamp=stamp,
                        source="gamepad",
                    )
                )
        elif was and not is_now:
            events.append(
                KeyEvent(
                    token=token,
                    kind="up",
                    modifiers=(PAD_MOD,) if modifier_held else (),
                    vk=bit,
                    timestamp=stamp,
                    source="gamepad",
                )
            )
    return events


class GamepadBackend:
    """手柄后端接口（测试可注入 fake）。

    ``available`` = 驱动/设备是否可用；``connected`` = 当前是否真有手柄在线
    （XInput 热插拔时轮询会从 connected 变 True/False，服务只透传事件）。
    """

    mode = "off"
    name = "null"

    @property
    def available(self) -> bool:
        return False

    @property
    def connected(self) -> bool:
        return False

    def poll(self) -> list[KeyEvent]:
        return []

    def close(self) -> None:
        pass


class NullGamepadBackend(GamepadBackend):
    def __init__(self, mode: str = "off"):
        self.mode = mode


# -- XInput -----------------------------------------------------------------
if _IS_WINDOWS:

    class XINPUT_GAMEPAD(ctypes.Structure):
        _fields_ = [
            ("wButtons", ctypes.c_ushort),
            ("bLeftTrigger", ctypes.c_ubyte),
            ("bRightTrigger", ctypes.c_ubyte),
            ("sThumbLX", ctypes.c_short),
            ("sThumbLY", ctypes.c_short),
            ("sThumbRX", ctypes.c_short),
            ("sThumbRY", ctypes.c_short),
        ]

    class XINPUT_STATE(ctypes.Structure):
        _fields_ = [
            ("dwPacketNumber", wintypes.DWORD),
            ("Gamepad", XINPUT_GAMEPAD),
        ]

else:  # pragma: no cover - 非 Windows 只为可导入
    class XINPUT_GAMEPAD(ctypes.Structure):  # type: ignore[no-redef]
        _fields_ = [
            ("wButtons", ctypes.c_ushort),
            ("bLeftTrigger", ctypes.c_ubyte),
            ("bRightTrigger", ctypes.c_ubyte),
            ("sThumbLX", ctypes.c_short),
            ("sThumbLY", ctypes.c_short),
            ("sThumbRX", ctypes.c_short),
            ("sThumbRY", ctypes.c_short),
        ]

    class XINPUT_STATE(ctypes.Structure):  # type: ignore[no-redef]
        _fields_ = [("dwPacketNumber", ctypes.c_ulong), ("Gamepad", XINPUT_GAMEPAD)]


_XINPUT_LIB = None
_XINPUT_LOADED = False


def load_xinput():
    """加载 XInput DLL；非 Windows 或加载失败返回 None（降级）。"""
    global _XINPUT_LIB, _XINPUT_LOADED
    if _XINPUT_LOADED:
        return _XINPUT_LIB
    _XINPUT_LOADED = True
    if not _IS_WINDOWS:
        return None
    for dll_name in ("xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll"):
        try:
            lib = ctypes.WinDLL(dll_name)
        except OSError:
            continue
        try:
            lib.XInputGetState.argtypes = [
                wintypes.DWORD,
                ctypes.POINTER(XINPUT_STATE),
            ]
            lib.XInputGetState.restype = wintypes.DWORD
        except AttributeError:
            continue
        _XINPUT_LIB = lib
        return lib
    return None


class XInputGamepadBackend(GamepadBackend):
    name = "xinput"
    mode = "xbox"

    def __init__(self) -> None:
        self._lib = load_xinput()
        self._slot: int | None = None
        self._previous = 0
        self._connected = False

    @property
    def available(self) -> bool:
        return self._lib is not None

    @property
    def connected(self) -> bool:
        return self._connected

    def _read(self, slot: int) -> tuple[int, int]:
        assert self._lib is not None
        state = XINPUT_STATE()
        code = int(self._lib.XInputGetState(slot, ctypes.byref(state)))
        if code != 0:
            return code, 0
        return 0, int(state.Gamepad.wButtons)

    def poll(self) -> list[KeyEvent]:
        if self._lib is None:
            return []
        try:
            if self._slot is not None:
                code, buttons = self._read(self._slot)
                if code != 0:
                    self._slot = None
            else:
                code, buttons = 1, 0
            if self._slot is None:
                for slot in range(4):
                    code, buttons = self._read(slot)
                    if code == 0:
                        self._slot = slot
                        break
                else:
                    if self._connected:
                        events = diff_gamepad(
                            self._previous, 0, XINPUT_BUTTON_MAP, modifier_bit=XINPUT_LB
                        )
                        self._previous = 0
                        self._connected = False
                        return events
                    return []
            self._connected = True
            previous = self._previous
            self._previous = buttons
            return diff_gamepad(
                previous, buttons, XINPUT_BUTTON_MAP, modifier_bit=XINPUT_LB
            )
        except Exception as exc:  # noqa: BLE001 - 手柄异常不能让应用崩
            log.warning("xinput poll failed: %s", exc)
            return []


# -- winmm joyGetPosEx -------------------------------------------------------
if _IS_WINDOWS:

    class JOYINFOEX(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("dwXpos", wintypes.DWORD),
            ("dwYpos", wintypes.DWORD),
            ("dwZpos", wintypes.DWORD),
            ("dwRpos", wintypes.DWORD),
            ("dwUpos", wintypes.DWORD),
            ("dwVpos", wintypes.DWORD),
            ("dwButtons", wintypes.DWORD),
            ("dwButtonNumber", wintypes.DWORD),
            ("dwPOV", wintypes.DWORD),
            ("dwReserved1", wintypes.DWORD),
            ("dwReserved2", wintypes.DWORD),
        ]

else:  # pragma: no cover
    class JOYINFOEX(ctypes.Structure):  # type: ignore[no-redef]
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("dwFlags", ctypes.c_ulong),
            ("dwXpos", ctypes.c_ulong),
            ("dwYpos", ctypes.c_ulong),
            ("dwZpos", ctypes.c_ulong),
            ("dwRpos", ctypes.c_ulong),
            ("dwUpos", ctypes.c_ulong),
            ("dwVpos", ctypes.c_ulong),
            ("dwButtons", ctypes.c_ulong),
            ("dwButtonNumber", ctypes.c_ulong),
            ("dwPOV", ctypes.c_ulong),
            ("dwReserved1", ctypes.c_ulong),
            ("dwReserved2", ctypes.c_ulong),
        ]


JOY_RETURNALL = 0x000000FF
_WINMM_LIB = None
_WINMM_LOADED = False


def load_winmm():
    global _WINMM_LIB, _WINMM_LOADED
    if _WINMM_LOADED:
        return _WINMM_LIB
    _WINMM_LOADED = True
    if not _IS_WINDOWS:
        return None
    try:
        lib = ctypes.WinDLL("winmm")
        lib.joyGetNumDevs.argtypes = []
        lib.joyGetNumDevs.restype = wintypes.UINT
        lib.joyGetPosEx.argtypes = [wintypes.UINT, ctypes.POINTER(JOYINFOEX)]
        lib.joyGetPosEx.restype = wintypes.UINT
    except (OSError, AttributeError):
        return None
    _WINMM_LIB = lib
    return lib


class WinMMGamepadBackend(GamepadBackend):
    name = "winmm"

    def __init__(self, mode: str = "ds") -> None:
        self.mode = "ds" if mode == "ds" else "xbox"
        self._lib = load_winmm()
        self._mapping = WINMM_DS_MAP if self.mode == "ds" else WINMM_XBOX_MAP
        self._modifier_bit = 0x0010  # B5 / L1 / LB
        self._slot: int | None = None
        self._previous = 0
        self._connected = False
        self._num_devices = 0
        if self._lib is not None:
            try:
                self._num_devices = int(self._lib.joyGetNumDevs())
            except Exception:  # noqa: BLE001
                self._num_devices = 0

    @property
    def available(self) -> bool:
        return self._lib is not None and self._num_devices > 0

    @property
    def connected(self) -> bool:
        return self._connected

    def _read(self, slot: int) -> tuple[int, int]:
        assert self._lib is not None
        info = JOYINFOEX()
        info.dwSize = ctypes.sizeof(JOYINFOEX)
        info.dwFlags = JOY_RETURNALL
        code = int(self._lib.joyGetPosEx(slot, ctypes.byref(info)))
        if code != 0:
            return code, 0
        return 0, int(info.dwButtons)

    def poll(self) -> list[KeyEvent]:
        if self._lib is None or self._num_devices <= 0:
            return []
        try:
            if self._slot is not None:
                code, buttons = self._read(self._slot)
                if code != 0:
                    self._slot = None
            else:
                code, buttons = 1, 0
            if self._slot is None:
                for slot in range(min(4, self._num_devices + 1)):
                    code, buttons = self._read(slot)
                    if code == 0:
                        self._slot = slot
                        break
                else:
                    if self._connected:
                        events = diff_gamepad(
                            self._previous,
                            0,
                            self._mapping,
                            modifier_bit=self._modifier_bit,
                        )
                        self._previous = 0
                        self._connected = False
                        return events
                    return []
            self._connected = True
            previous = self._previous
            self._previous = buttons
            return diff_gamepad(
                previous, buttons, self._mapping, modifier_bit=self._modifier_bit
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("winmm poll failed: %s", exc)
            return []


def create_gamepad_backend(mode: object = "off") -> GamepadBackend:
    """按模式创建可用后端；不可用时返回 Null（服务会发 warning，不崩溃）。"""
    normalized = normalize_gamepad_mode(mode)
    if normalized == "off":
        return NullGamepadBackend("off")
    factories: tuple[Callable[[], GamepadBackend], ...]
    if normalized == "ds":
        factories = (
            lambda: WinMMGamepadBackend("ds"),
            lambda: XInputGamepadBackend(),
        )
    else:
        factories = (
            lambda: XInputGamepadBackend(),
            lambda: WinMMGamepadBackend("xbox"),
        )
    for factory in factories:
        try:
            backend = factory()
        except Exception:  # noqa: BLE001 - 加载失败就试下一个
            log.exception("gamepad backend init failed")
            continue
        if backend.available:
            return backend
    log.info("gamepad mode %s unavailable, degraded to off", normalized)
    return NullGamepadBackend(normalized)
