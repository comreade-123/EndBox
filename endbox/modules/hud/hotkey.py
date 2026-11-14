# -*- coding: utf-8 -*-
"""全局热键：``RegisterHotKey`` + ``QAbstractNativeEventFilter``。

用法::

    hk = GlobalHotkey()
    hk.activated.connect(on_hotkey)
    ok = hk.register("Ctrl+Alt+H")     # 失败返回 False，原因在 hk.last_error
    ...
    hk.unregister()

注册失败（被其他程序占用 / 没有 QApplication / 非 Windows）不会抛异常：
写日志、返回 False、把原因放进 ``last_error`` 并发出 ``failed`` 信号，
调用方负责在界面上给用户一句人话提示。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PyQt6.QtCore import QAbstractNativeEventFilter, QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QWidget

from endbox.core.log import get_logger
from endbox.core.text import t

log = get_logger("hud.hotkey")

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

_MODS = {
    "CTRL": MOD_CONTROL,
    "CONTROL": MOD_CONTROL,
    "ALT": MOD_ALT,
    "SHIFT": MOD_SHIFT,
    "WIN": MOD_WIN,
    "META": MOD_WIN,
}
_MOD_ORDER = ((MOD_CONTROL, "Ctrl"), (MOD_ALT, "Alt"), (MOD_SHIFT, "Shift"), (MOD_WIN, "Win"))

_VK: dict[str, int] = {}
for _i in range(26):
    _VK[chr(0x41 + _i)] = 0x41 + _i
for _d in range(10):
    _VK[str(_d)] = 0x30 + _d
for _i in range(1, 25):
    _VK[f"F{_i}"] = 0x70 + _i - 1
_VK.update(
    {
        "SPACE": 0x20,
        "TAB": 0x09,
        "ENTER": 0x0D,
        "ESC": 0x1B,
        "ESCAPE": 0x1B,
        "HOME": 0x24,
        "END": 0x23,
        "PAGEUP": 0x21,
        "PAGEDOWN": 0x22,
        "INSERT": 0x2D,
        "DELETE": 0x2E,
        "LEFT": 0x25,
        "UP": 0x26,
        "RIGHT": 0x27,
        "DOWN": 0x28,
        "-": 0xBD,
        "=": 0xBB,
        "[": 0xDB,
        "]": 0xDD,
        "`": 0xC0,
        ";": 0xBA,
        "'": 0xDE,
        ",": 0xBC,
        ".": 0xBE,
        "/": 0xBF,
        "\\": 0xDC,
    }
)
_VK_REVERSE: dict[int, str] = {}
for _name, _code in _VK.items():
    _VK_REVERSE.setdefault(_code, _name)

# 显示用的人话按键名（解析时用上面的别名表）
_VK_DISPLAY = {
    0x20: "Space",
    0x09: "Tab",
    0x0D: "Enter",
    0x1B: "Esc",
    0x24: "Home",
    0x23: "End",
    0x21: "PgUp",
    0x22: "PgDn",
    0x2D: "Ins",
    0x2E: "Del",
    0x25: "Left",
    0x26: "Up",
    0x27: "Right",
    0x28: "Down",
}
_VK.setdefault("PGUP", 0x21)
_VK.setdefault("PGDN", 0x22)
_VK.setdefault("INS", 0x2D)
_VK.setdefault("DEL", 0x2E)


class HotkeyError(ValueError):
    """热键字符串不合法（人话原因）。"""


def _clean_parts(spec: str) -> list[str]:
    return [part.strip() for part in str(spec or "").split("+") if part.strip()]


def parse_hotkey(spec: str) -> tuple[int, int]:
    """``"Ctrl+Alt+H"`` → ``(modifiers, virtual_key)``；不合法抛 :class:`HotkeyError`。"""
    parts = _clean_parts(spec)
    if not parts:
        raise HotkeyError(t("hud.errors.hotkey_empty"))
    mods = 0
    key: int | None = None
    key_name = ""
    for part in parts:
        upper = part.upper()
        if upper in _MODS:
            mods |= _MODS[upper]
            continue
        vk = _VK.get(upper)
        if vk is None:
            raise HotkeyError(t("hud.errors.hotkey_unknown_key", name=part))
        if key is not None:
            raise HotkeyError(t("hud.errors.hotkey_multiple_keys", spec=spec))
        key = vk
        key_name = part
    if key is None:
        raise HotkeyError(t("hud.errors.hotkey_missing_key", spec=spec))
    if mods == 0:
        raise HotkeyError(t("hud.errors.hotkey_need_modifier"))
    return mods, key


def display_hotkey(mods: int, vk: int) -> str:
    """``(MOD_CONTROL|MOD_ALT, 0x48)`` → ``"Ctrl+Alt+H"``。"""
    names = [name for bit, name in _MOD_ORDER if mods & bit]
    names.append(_VK_DISPLAY.get(vk) or _VK_REVERSE.get(vk, f"VK{vk:02X}"))
    return "+".join(names)


def normalize_hotkey(spec: str) -> str:
    """校验并规范化为显示形式；不合法抛 :class:`HotkeyError`。"""
    mods, vk = parse_hotkey(spec)
    return display_hotkey(mods, vk)


# ---------------------------------------------------------------------------
# 从 Qt 键盘事件生成热键字符串（控制页"录制热键"用）
# ---------------------------------------------------------------------------

_QT_SPECIAL_KEYS = {
    Qt.Key.Key_Space: "SPACE",
    Qt.Key.Key_Tab: "TAB",
    Qt.Key.Key_Return: "ENTER",
    Qt.Key.Key_Enter: "ENTER",
    Qt.Key.Key_Escape: "ESC",
    Qt.Key.Key_Home: "HOME",
    Qt.Key.Key_End: "END",
    Qt.Key.Key_PageUp: "PAGEUP",
    Qt.Key.Key_PageDown: "PAGEDOWN",
    Qt.Key.Key_Insert: "INSERT",
    Qt.Key.Key_Delete: "DELETE",
    Qt.Key.Key_Left: "LEFT",
    Qt.Key.Key_Up: "UP",
    Qt.Key.Key_Right: "RIGHT",
    Qt.Key.Key_Down: "DOWN",
    Qt.Key.Key_Minus: "-",
    Qt.Key.Key_Equal: "=",
    Qt.Key.Key_BracketLeft: "[",
    Qt.Key.Key_BracketRight: "]",
    Qt.Key.Key_QuoteLeft: "`",
    Qt.Key.Key_Semicolon: ";",
    Qt.Key.Key_Apostrophe: "'",
    Qt.Key.Key_Comma: ",",
    Qt.Key.Key_Period: ".",
    Qt.Key.Key_Slash: "/",
    Qt.Key.Key_Backslash: "\\",
}


def qt_key_name(key) -> str | None:
    """``Qt.Key`` → 我们热键语法里的主键名；不支持的键返回 None。"""
    try:
        code = int(key)
    except (TypeError, ValueError):
        return None
    if 0x41 <= code <= 0x5A:  # A–Z
        return chr(code)
    if 0x30 <= code <= 0x39:  # 0–9
        return chr(code)
    f1 = int(Qt.Key.Key_F1)
    if f1 <= code <= int(Qt.Key.Key_F24):
        return f"F{code - f1 + 1}"
    try:
        qt_key = Qt.Key(code)
    except ValueError:
        return None
    name = _QT_SPECIAL_KEYS.get(qt_key)  # type: ignore[arg-type]
    return name


def mods_from_qt(modifiers) -> int:
    """``Qt.KeyboardModifier`` 位 → Win32 ``MOD_*`` 位。"""
    # PyQt6 6.11 的 KeyboardModifier 是 enum.Flag（不能 int()），这里统一成 flag 对象再按位判断
    try:
        flags = modifiers if isinstance(modifiers, Qt.KeyboardModifier) else Qt.KeyboardModifier(modifiers)
    except (TypeError, ValueError):
        return 0

    def has(flag) -> bool:
        try:
            return bool(flags & flag)
        except TypeError:
            return False

    mods = 0
    if has(Qt.KeyboardModifier.ControlModifier):
        mods |= MOD_CONTROL
    if has(Qt.KeyboardModifier.AltModifier):
        mods |= MOD_ALT
    if has(Qt.KeyboardModifier.ShiftModifier):
        mods |= MOD_SHIFT
    if has(Qt.KeyboardModifier.MetaModifier):
        mods |= MOD_WIN
    return mods


def spec_from_qt(key, modifiers) -> str | None:
    """Qt 键盘事件 → ``"Ctrl+Alt+H"``；按键不支持时返回 None。"""
    name = qt_key_name(key)
    if name is None:
        return None
    vk = _VK.get(name)
    if vk is None:
        return None
    return display_hotkey(mods_from_qt(modifiers), vk)


def is_valid_spec(spec: str) -> bool:
    try:
        normalize_hotkey(spec)
    except HotkeyError:
        return False
    return True


def _user32():
    """Win32 user32（测试里替换成抛异常的实现来覆盖失败分支）。"""
    return ctypes.WinDLL("user32", use_last_error=True)


def _is_windows_message(event_type) -> bool:
    try:
        text = bytes(event_type).decode("latin-1", "ignore")
    except Exception:  # noqa: BLE001 - 不是 bytes 时退化成 str
        text = str(event_type)
    return "windows" in text


class HotkeyNativeFilter(QAbstractNativeEventFilter):
    """把原生 ``WM_HOTKEY`` 转给 :class:`GlobalHotkey`。

    PyQt6 6.5+ 的虚函数返回类型是 ``(bool, sip.voidptr)``（消费标记 + 原消息指针），
    只返回 bool 会触发 "invalid result from nativeEventFilter()" 的 TypeError，
    进而打断整个 Qt 事件循环。这里始终返回二元组，异常也降级为 ``(False, message)``。
    """

    def __init__(self, owner: "GlobalHotkey"):
        super().__init__()
        self._owner = owner

    def nativeEventFilter(self, event_type, message):  # noqa: N802 - Qt 接口
        handled = False
        try:
            handled = bool(self._owner.handle_native_message(event_type, message))
        except Exception:  # noqa: BLE001 - 原生回调里绝不能让异常回到 Qt
            log.exception("native hotkey filter failed")
            handled = False
        return (handled, message)


class GlobalHotkey(QObject):
    """一个全局热键。同一实例重复 register 会先注销旧的。"""

    activated = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._registered = False
        self._spec = ""
        self._mods = 0
        self._vk = 0
        self._last_error = ""
        # 热键 id：进程内每个实例一个，0x0000–0xBFFF 是应用私有区间
        self._hotkey_id = 0xEA00 + (id(self) % 0x1F)
        self._owner_widget: QWidget | None = None
        self._filter: HotkeyNativeFilter | None = None

    # -- 状态 ---------------------------------------------------------------
    @property
    def registered(self) -> bool:
        return self._registered

    @property
    def spec(self) -> str:
        return self._spec

    @property
    def last_error(self) -> str:
        return self._last_error

    # -- 注册 / 注销 ---------------------------------------------------------
    def register(self, spec: str) -> bool:
        """注册（或重注册）热键。返回 False 时看 :attr:`last_error`。"""
        self.unregister()
        try:
            mods, vk = parse_hotkey(spec)
        except HotkeyError as exc:
            return self._fail(str(exc))
        self._spec = display_hotkey(mods, vk)
        self._mods = mods
        self._vk = vk
        self._last_error = ""

        if not sys.platform.startswith("win"):
            return self._fail(t("hud.errors.hotkey_windows_only"))
        app = QApplication.instance()
        if app is None:
            return self._fail(t("hud.errors.hotkey_no_app"))

        try:
            # RegisterHotKey 需要一个窗口句柄；用一个永远不显示的隐藏窗口
            owner = QWidget(None, Qt.WindowType.Tool)
            owner.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            owner.setWindowTitle("EndBox Hotkey Host")
            owner.resize(0, 0)
            hwnd = int(owner.winId())
            if not hwnd:
                raise RuntimeError(t("hud.errors.hotkey_no_hwnd"))
            self._owner_widget = owner

            self._filter = HotkeyNativeFilter(self)
            app.installNativeEventFilter(self._filter)

            user32 = _user32()
            user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
            user32.RegisterHotKey.restype = wintypes.BOOL
            ok = user32.RegisterHotKey(wintypes.HWND(hwnd), self._hotkey_id, mods | MOD_NOREPEAT, vk)
            if not ok:
                raise OSError(ctypes.get_last_error() or 0, t("hud.errors.hotkey_register_failed"))
        except Exception as exc:  # noqa: BLE001 - 失败必须走界面提示，不能崩
            self._teardown()
            return self._fail(self._explain(exc))

        self._registered = True
        log.info("global hotkey registered: %s", self._spec)
        return True

    def unregister(self) -> None:
        """注销热键并销毁隐藏宿主窗口（可重复调用）。"""
        if self._filter is not None:
            app = QApplication.instance()
            if app is not None:
                try:
                    app.removeNativeEventFilter(self._filter)
                except Exception:  # noqa: BLE001
                    pass
            self._filter = None
        owner = self._owner_widget
        if owner is not None:
            if self._registered and sys.platform.startswith("win"):
                try:
                    user32 = _user32()
                    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
                    user32.UnregisterHotKey.restype = wintypes.BOOL
                    user32.UnregisterHotKey(wintypes.HWND(int(owner.winId())), self._hotkey_id)
                except Exception:  # noqa: BLE001
                    log.exception("unregister hotkey failed")
            self._owner_widget = None
            owner.deleteLater()
        self._registered = False

    def _teardown(self) -> None:
        self._registered = False
        self.unregister()

    def _fail(self, message: str) -> bool:
        self._last_error = message
        log.warning("global hotkey unavailable (%s): %s", self._spec or "?", message)
        self.failed.emit(message)
        return False

    @staticmethod
    def _explain(exc: Exception) -> str:
        code = getattr(exc, "winerror", None) or (exc.args[0] if exc.args and isinstance(exc.args[0], int) else 0)
        if code == ERROR_HOTKEY_ALREADY_REGISTERED or "1409" in str(exc):
            return t("hud.errors.hotkey_occupied")
        if isinstance(exc, OSError) and getattr(exc, "winerror", None):
            return t("hud.errors.hotkey_register_error", error=str(exc.strerror or exc))
        return t("hud.errors.hotkey_register_error", error=str(exc))

    # -- 原生事件 -----------------------------------------------------------
    def handle_native_message(self, event_type, message) -> bool:
        if not self._registered or not _is_windows_message(event_type):
            return False
        try:
            address = int(message)  # PyQt6 给的是 sip.voidptr
        except (TypeError, ValueError):
            try:  # 也接受 ctypes 指针（测试与其它宿主用得到）
                address = int(ctypes.cast(message, ctypes.c_void_p).value or 0)
            except Exception:  # noqa: BLE001
                return False
        if not address:
            return False
        try:
            msg = ctypes.cast(address, ctypes.POINTER(wintypes.MSG)).contents
        except Exception:  # noqa: BLE001 - 指针不是 MSG 时忽略
            return False
        try:
            if int(msg.message) == WM_HOTKEY and int(msg.wParam) == self._hotkey_id:
                self.activated.emit()
                return True
        except Exception:  # noqa: BLE001
            return False
        return False
