# -*- coding: utf-8 -*-
"""键盘输入：Windows 低级键盘钩子（WH_KEYBOARD_LL）+ 独立线程消息循环。

设计要点：
  · 钩子回调只做 VK → token 的轻量解析，然后通过回调把 ``KeyEvent`` 交给
    ``InputService``（服务里再用 Qt signal 发回主线程）；回调里绝不阻塞；
  · 修饰键状态（Alt / Q / E）在钩子线程里自己维护，不调用 GetAsyncKeyState；
  · 自动重复的 keydown 被忽略（只报第一次按下）；
  · 长按判定（终结技 ``HOLD+n``）集中在纯 Python 的 ``HoldTracker`` 里，由服务
    在主线程用 QTimer 驱动，既可单测，也避免钩子线程做计时；
  · ``stop()`` 用 ``PostThreadMessage(WM_QUIT)`` 结束消息循环，钩子在线程的
    ``finally`` 里 ``UnhookWindowsHookEx``，然后 join 线程；重复 start/stop 不泄漏。

非 Windows 平台 ``start()`` 会抛 ``InputBackendError``，由服务降级（``NullKeyboardBackend``）。
"""
from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes
from typing import Callable, Iterable

from endbox.core.log import get_logger
from endbox.core.input.models import InputBackendError, KeyEvent
from endbox.core.input.tokens import DIGITS, digit_of

log = get_logger("core.input.keyboard")

_IS_WINDOWS = sys.platform.startswith("win")

# -- 长按（终结技）参数 -------------------------------------------------------
HOLD_MS_DEFAULT = 280
HOLD_MS_MIN = 80
HOLD_MS_MAX = 2000

# Win32 常量
WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_QUIT = 0x0012
LLKHF_EXTENDED = 0x01
LLKHF_INJECTED = 0x10
LLKHF_UP = 0x80

# 虚拟键码
VK_ALT = 0x12
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_Q = 0x51
VK_E = 0x45
VK_H = 0x48
VK_F1 = 0x70
VK_F2 = 0x71
VK_F3 = 0x72
VK_F4 = 0x73
VK_F6 = 0x75
VK_F7 = 0x76
VK_F8 = 0x77
VK_F9 = 0x78
VK_F10 = 0x79
VK_F11 = 0x7A
VK_1, VK_2, VK_3, VK_4 = 0x31, 0x32, 0x33, 0x34

# 修饰键 vkCode：低级钩子对左右 Alt 可能报告 VK_MENU / VK_LMENU / VK_RMENU，全部算 ALT
_MODIFIER_VKS: dict[int, str] = {
    VK_ALT: "ALT",
    VK_LMENU: "ALT",
    VK_RMENU: "ALT",
    VK_Q: "Q",
    VK_E: "E",
}
_DIGIT_VKS: dict[int, str] = {VK_1: "1", VK_2: "2", VK_3: "3", VK_4: "4"}
_MODIFIER_ORDER: tuple[str, ...] = ("ALT", "Q", "E")
_MODIFIER_NAMES = frozenset(_MODIFIER_ORDER)

_VK_TO_BASE: dict[int, str] = {
    VK_Q: "Q",
    VK_E: "E",
    VK_ALT: "ALT",
    VK_LMENU: "ALT",
    VK_RMENU: "ALT",
    VK_H: "H",
    VK_1: "1",
    VK_2: "2",
    VK_3: "3",
    VK_4: "4",
    VK_F1: "F1",
    VK_F2: "F2",
    VK_F3: "F3",
    VK_F4: "F4",
    VK_F6: "F6",
    VK_F7: "F7",
    VK_F8: "F8",
    VK_F9: "F9",
    VK_F10: "F10",
    VK_F11: "F11",
}


# ---------------------------------------------------------------------------
# 纯函数：VK → token
# ---------------------------------------------------------------------------
def base_token_for_vk(vk: int) -> str:
    """VK → 不带组合的 token；未知键返回空字符串。"""
    return _VK_TO_BASE.get(int(vk), "")


def digit_for_vk(vk: int) -> str:
    """VK → 数字槽（1-4）；不是数字键返回空字符串。"""
    return _DIGIT_VKS.get(int(vk), "")


def vk_for_digit(digit: int | str) -> int:
    """数字槽 1-4 → 主键盘 VK（0x31-0x34）。"""
    value = str(digit).strip()
    if value not in DIGITS:
        raise ValueError(f"digit 必须是 1-4，收到 {digit!r}")
    return VK_1 + int(value) - 1


def token_for_key(vk: int, modifiers: Iterable[str] | None = None) -> str:
    """按下瞬间的 VK + 修饰键 → 规范 token。

    - 数字 1-4：无修饰 → ``1``；按住 Q/E/ALT → ``Q+1`` / ``E+1`` / ``ALT+1``
      （组合优先级 ``ALT`` > ``Q`` > ``E``）；
    - ``H`` → ``H``；``F1``..``F4``（切换干员）与 ``F6``..``F11`` → 同名 token；
    - 修饰键本身、未绑定的键 → 空字符串（不产生动作 token）。
    """
    base = base_token_for_vk(vk)
    if not base:
        return ""
    if base in ("Q", "E", "ALT"):
        return ""
    held = {str(item).upper() for item in (modifiers or ())}
    if base in DIGITS:
        for modifier in _MODIFIER_ORDER:
            if modifier in held:
                return f"{modifier}+{base}"
    return base


class ModifierTracker:
    """钩子线程内的修饰键状态机（纯 Python，可单测）。"""

    def __init__(self) -> None:
        self._held: set[str] = set()

    def reset(self) -> None:
        self._held.clear()

    def update(self, vk: int, down: bool) -> tuple[str, ...]:
        name = _MODIFIER_VKS.get(int(vk))
        if name:
            if down:
                self._held.add(name)
            else:
                self._held.discard(name)
        return self.modifiers()

    def modifiers(self) -> tuple[str, ...]:
        return tuple(name for name in _MODIFIER_ORDER if name in self._held)

    @property
    def held(self) -> frozenset[str]:
        return frozenset(self._held)


# ---------------------------------------------------------------------------
# 长按（终结技）判定：纯函数式状态机，服务在主线程用 QTimer 调 due()
# ---------------------------------------------------------------------------
class HoldTracker:
    """数字键长按 ≥ ``hold_ms`` 判定。

    只有**无修饰键**按下的数字才参与长按（``HOLD+n`` 是终结技的长按路线，
    和 ``ALT+n`` 等价）；按住 Q/E/ALT 时按数字是组合键，不触发 HOLD，
    避免一次按键同时命中技能 / 终结技两个语义。

    用法（由 ``InputService`` 驱动）::

        tracker.feed(down_event)      # KeyEvent(kind="down")
        tracker.due()                 # 返回刚超过阈值的数字槽，如 ["1"]
        tracker.feed(up_event)        # 松开，取消待判定状态
    """

    def __init__(self, hold_ms: int = HOLD_MS_DEFAULT, clock: Callable[[], float] | None = None):
        self._hold_ms = _clamp_hold_ms(hold_ms)
        self._clock = clock or time.monotonic
        # digit -> (按下时间, 是否参与长按)
        self._pending: dict[str, tuple[float, bool]] = {}
        self._fired: set[str] = set()

    # -- 配置 ---------------------------------------------------------------
    @property
    def hold_ms(self) -> int:
        return self._hold_ms

    def set_hold_ms(self, hold_ms: object) -> int:
        self._hold_ms = _clamp_hold_ms(hold_ms)
        return self._hold_ms

    # -- 状态 ---------------------------------------------------------------
    def reset(self) -> None:
        self._pending.clear()
        self._fired.clear()

    @property
    def pending(self) -> dict[str, tuple[float, bool]]:
        return dict(self._pending)

    def press(self, digit: int | str, modifiers: Iterable[str] | None = None) -> None:
        value = str(digit).strip()
        if value not in DIGITS:
            return
        if value in self._pending:  # 自动重复：保留第一次按下的时间
            return
        held = {str(item).upper() for item in (modifiers or ())}
        eligible = not (held & _MODIFIER_NAMES)
        self._pending[value] = (self._clock(), eligible)

    def release(self, digit: int | str) -> None:
        value = str(digit).strip()
        self._pending.pop(value, None)
        self._fired.discard(value)

    def feed(self, event: KeyEvent) -> None:
        """吃一个键盘 KeyEvent（down/up），非数字事件直接忽略。"""
        if event.source != "keyboard":
            return
        digit = digit_for_vk(event.vk) or digit_of(event.token)
        if not digit:
            return
        if event.kind == "down":
            self.press(digit, event.modifiers)
        elif event.kind == "up":
            self.release(digit)

    def due(self) -> list[str]:
        """返回本次刚超过 hold_ms 且仍按住的无修饰数字槽（每个只报一次）。"""
        now = self._clock()
        threshold = self._hold_ms / 1000.0
        ready: list[str] = []
        for digit in DIGITS:
            item = self._pending.get(digit)
            if item is None or digit in self._fired:
                continue
            started, eligible = item
            if eligible and (now - started) >= threshold:
                self._fired.add(digit)
                ready.append(digit)
        return ready


def _clamp_hold_ms(value: object) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return HOLD_MS_DEFAULT
    return max(HOLD_MS_MIN, min(HOLD_MS_MAX, number))


# ---------------------------------------------------------------------------
# 后端接口
# ---------------------------------------------------------------------------
class KeyboardBackend:
    """键盘后端接口（测试可注入 fake）。"""

    name = "base"

    @property
    def available(self) -> bool:
        return False

    def start(self, emit: Callable[[KeyEvent], None]) -> None:  # pragma: no cover - 接口
        raise InputBackendError("键盘后端未实现")

    def stop(self) -> None:  # pragma: no cover - 接口
        pass


class NullKeyboardBackend(KeyboardBackend):
    """非 Windows / 钩子不可用时的降级后端：start 抛可显示的中文原因。"""

    name = "null"

    def start(self, emit: Callable[[KeyEvent], None]) -> None:
        raise InputBackendError("当前平台不支持全局键盘钩子（仅 Windows），输入服务已降级。")


if _IS_WINDOWS:

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
    )

else:  # pragma: no cover - 非 Windows 只为可导入
    KBDLLHOOKSTRUCT = None
    LowLevelKeyboardProc = None


class WindowsKeyboardBackend(KeyboardBackend):
    """WH_KEYBOARD_LL 钩子后端。回调在钩子线程里执行，只做轻量解析。"""

    name = "windows-low-level"

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hook = None
        self._proc = None
        self._emit: Callable[[KeyEvent], None] | None = None
        self._ready = threading.Event()
        self._start_error: str | None = None
        self._running = False
        self._pressed: set[int] = set()
        self._modifiers = ModifierTracker()
        self._user32 = None
        self._kernel32 = None

    @property
    def available(self) -> bool:
        return _IS_WINDOWS

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(self._running and thread is not None and thread.is_alive())

    # -- 生命周期 -----------------------------------------------------------
    def start(self, emit: Callable[[KeyEvent], None]) -> None:
        if not _IS_WINDOWS:
            raise InputBackendError("全局键盘钩子仅支持 Windows，其他平台已降级为不可用。")
        if self.running:
            return
        self._emit = emit
        self._ready.clear()
        self._start_error = None
        self._pressed.clear()
        self._modifiers.reset()
        self._thread = threading.Thread(
            target=self._run, name="endbox-input-hook", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(3.0):
            self._thread = None
            raise InputBackendError("键盘钩子线程启动超时（3 秒）。")
        if self._start_error:
            message = self._start_error
            self._thread = None
            self._thread_id = 0
            raise InputBackendError(message)

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            self._running = False
            return
        self._running = False
        thread_id = self._thread_id
        if thread_id and self._user32 is not None:
            try:
                self._user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
            except Exception:  # noqa: BLE001
                log.exception("PostThreadMessage(WM_QUIT) failed")
        thread.join(timeout=2.0)
        if thread.is_alive():
            # 正常路径不会走到这里：线程会在 finally 里自己 Unhook。这里只记日志，
            # 不在别的线程强行 Unhook（钩子回调可能还在使用 hook 句柄）。
            log.warning("keyboard hook thread did not exit in 2s")
        self._thread = None
        self._thread_id = 0
        self._hook = None
        self._proc = None
        self._emit = None
        self._pressed.clear()
        self._modifiers.reset()

    # -- 钩子线程 -----------------------------------------------------------
    def _run(self) -> None:
        try:
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
            self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            user32 = self._user32
            kernel32 = self._kernel32
            user32.SetWindowsHookExW.argtypes = [
                ctypes.c_int,
                LowLevelKeyboardProc,
                wintypes.HINSTANCE,
                wintypes.DWORD,
            ]
            user32.SetWindowsHookExW.restype = wintypes.HHOOK
            user32.CallNextHookEx.argtypes = [
                wintypes.HHOOK,
                ctypes.c_int,
                wintypes.WPARAM,
                wintypes.LPARAM,
            ]
            user32.CallNextHookEx.restype = ctypes.c_ssize_t
            user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
            user32.UnhookWindowsHookEx.restype = wintypes.BOOL
            user32.GetMessageW.argtypes = [
                ctypes.POINTER(wintypes.MSG),
                wintypes.HWND,
                wintypes.UINT,
                wintypes.UINT,
            ]
            user32.GetMessageW.restype = ctypes.c_int
            user32.PostThreadMessageW.argtypes = [
                wintypes.DWORD,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            ]
            user32.PostThreadMessageW.restype = wintypes.BOOL
            user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user32.TranslateMessage.restype = wintypes.BOOL
            user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user32.DispatchMessageW.restype = ctypes.c_ssize_t
            # 必须声明 HMODULE 返回类型：默认 c_int 会把 64 位模块句柄截断，
            # SetWindowsHookEx 会以 ERROR_MOD_NOT_FOUND(126) 失败。
            kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
            kernel32.GetModuleHandleW.restype = wintypes.HMODULE

            self._thread_id = int(kernel32.GetCurrentThreadId())
            self._proc = LowLevelKeyboardProc(self._callback)
            module = kernel32.GetModuleHandleW(None)
            hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, module, 0)
            if not hook:
                # 少数环境不接受 hMod（例如打包后的句柄不可用）：按文档改传 NULL 重试。
                error_code = ctypes.get_last_error()
                log.warning("SetWindowsHookEx(hMod) 失败（%s），改用 NULL 重试", error_code)
                hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
            if not hook:
                error_code = ctypes.get_last_error()
                self._start_error = (
                    f"安装全局键盘钩子失败（SetWindowsHookEx 错误码 {error_code}）。"
                )
                log.error(self._start_error)
                return
            self._hook = hook
            self._running = True
            self._ready.set()
            log.info("global keyboard hook started (thread=%s)", self._thread_id)

            msg = wintypes.MSG()
            while self._running:
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result <= 0:  # 0 = WM_QUIT, -1 = error
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:  # noqa: BLE001 - 线程内异常必须兜住
            self._start_error = f"键盘钩子线程异常：{exc}"
            log.exception("keyboard hook thread failed")
        finally:
            self._running = False
            self._ready.set()
            hook = self._hook
            if hook and self._user32 is not None:
                try:
                    self._user32.UnhookWindowsHookEx(hook)
                except Exception:  # noqa: BLE001
                    log.exception("UnhookWindowsHookEx failed")
            self._hook = None
            log.info("global keyboard hook stopped")

    def _callback(self, n_code: int, w_param, l_param) -> int:
        try:
            if n_code == HC_ACTION and KBDLLHOOKSTRUCT is not None:
                data = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                self._handle(
                    int(data.vkCode),
                    is_up=bool(int(data.flags) & LLKHF_UP),
                    injected=bool(int(data.flags) & LLKHF_INJECTED),
                )
        except Exception:  # noqa: BLE001 - 回调里绝不抛异常
            log.exception("keyboard hook callback failed")
        try:
            return int(
                self._user32.CallNextHookEx(self._hook, n_code, w_param, l_param)
            )
        except Exception:  # noqa: BLE001
            return 0

    def _handle(self, vk: int, *, is_up: bool, injected: bool) -> None:
        now = time.monotonic()
        emit = self._emit
        if emit is None:
            return
        if is_up:
            self._pressed.discard(vk)
            modifiers = self._modifiers.update(vk, False)
            emit(
                KeyEvent(
                    token=token_for_key(vk, modifiers),
                    kind="up",
                    modifiers=modifiers,
                    vk=vk,
                    timestamp=now,
                    source="keyboard",
                    injected=injected,
                )
            )
            return
        if vk in self._pressed:  # 自动重复：忽略
            return
        self._pressed.add(vk)
        modifiers = self._modifiers.update(vk, True)
        emit(
            KeyEvent(
                token=token_for_key(vk, modifiers),
                kind="down",
                modifiers=modifiers,
                vk=vk,
                timestamp=now,
                source="keyboard",
                injected=injected,
            )
        )
