# -*- coding: utf-8 -*-
"""全局输入服务：Windows 低级键盘钩子 + 手柄轮询 → 规范化 token。

对外契约（排轴运行态等消费方直接使用）::

    from endbox.core.input import InputService
    svc = InputService()
    svc.token.connect(lambda t: print("token", t))      # 主线程回调
    svc.raw.connect(lambda ev: print(ev))               # KeyEvent（down/up/hold）
    svc.warning.connect(lambda text: print("warn", text))
    svc.start()                                         # 幂等
    ...
    svc.stop()                                          # 幂等，干净卸载钩子

线程模型：
  · 钩子回调在独立的 ``endbox-input-hook`` 线程里执行，只做 VK→token 的轻量解析；
  · 回调通过内部信号 ``_posted``（AutoConnection）把 ``KeyEvent`` 投递回本对象所在
    线程（主线程）。从其他线程 emit 时 Qt 自动排队，因此所有公开信号、订阅回调、
    长按判定都在主线程执行；
  · 手柄轮询是主线程里的 QTimer（XInputGetState / joyGetPosEx 都是很轻的本地调用）；
  · 钩子线程异常、后端启动失败、手柄不可用全部捕获并转发 error/warning 信号，不向上抛。

非 Windows：``start()`` 返回 False 并发出 error/warning（优雅降级），不装任何钩子。
"""
from __future__ import annotations

import ctypes
import sys
import threading
import time
from typing import Any, Callable

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from endbox.core.input.gamepad import (
    GAMEPAD_MODES,
    GamepadBackend,
    create_gamepad_backend,
    normalize_gamepad_mode,
)
from endbox.core.input.keyboard import (
    HOLD_MS_DEFAULT,
    HOLD_MS_MAX,
    HOLD_MS_MIN,
    HoldTracker,
    KeyboardBackend,
    NullKeyboardBackend,
    WindowsKeyboardBackend,
    vk_for_digit,
)
from endbox.core.input.models import InputBackendError, KeyEvent
from endbox.core.input.tokens import normalize_token
from endbox.core.log import get_logger

log = get_logger("core.input.service")

_IS_WINDOWS = sys.platform.startswith("win")

HOLD_CHECK_MS = 25          # 长按判定轮询间隔（比 hold_ms 小一个量级）
GAMEPAD_POLL_MS = 25        # 手柄轮询间隔（≈40Hz，人类感知不到延迟）
_START_TIMEOUT_S = 3.0

_ADMIN_CACHE: bool | None = None


# ---------------------------------------------------------------------------
# 管理员检测（Shell32 IsUserAnAdmin，失败退回 OpenProcessToken）
# ---------------------------------------------------------------------------
def _check_admin() -> bool:
    if not _IS_WINDOWS:
        return False
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.IsUserAnAdmin.argtypes = []
        shell32.IsUserAnAdmin.restype = ctypes.c_int
        return bool(shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001 - 老系统/被裁剪的 shell32
        log.exception("IsUserAnAdmin 调用失败，改用 OpenProcessToken")
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        advapi32.OpenProcessToken.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        advapi32.OpenProcessToken.restype = ctypes.c_int
        advapi32.GetTokenInformation.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        advapi32.GetTokenInformation.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int

        class _TokenElevation(ctypes.Structure):
            _fields_ = [("TokenIsElevated", ctypes.c_ulong)]

        process = kernel32.GetCurrentProcess()
        token = ctypes.c_void_p()
        TOKEN_QUERY = 0x0008
        if not advapi32.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
            return False
        try:
            elevation = _TokenElevation()
            written = ctypes.c_ulong(0)
            TOKEN_ELEVATION = 20
            ok = advapi32.GetTokenInformation(
                token,
                TOKEN_ELEVATION,
                ctypes.byref(elevation),
                ctypes.sizeof(elevation),
                ctypes.byref(written),
            )
            return bool(ok and elevation.TokenIsElevated)
        finally:
            kernel32.CloseHandle(token)
    except Exception:  # noqa: BLE001
        log.exception("OpenProcessToken 管理员检测失败")
    return False


def is_admin() -> bool:
    """当前进程是否以管理员身份运行（结果缓存；非 Windows 返回 False）。"""
    global _ADMIN_CACHE
    if _ADMIN_CACHE is None:
        _ADMIN_CACHE = _check_admin()
    return bool(_ADMIN_CACHE)


# ---------------------------------------------------------------------------
# 订阅句柄
# ---------------------------------------------------------------------------
class Subscription:
    """``InputService.subscribe()`` 返回的句柄；``cancel()`` 幂等。"""

    __slots__ = ("_service", "_sid", "_active")

    def __init__(self, service: "InputService", sid: int):
        self._service = service
        self._sid = sid
        self._active = True

    @property
    def active(self) -> bool:
        return self._active

    def cancel(self) -> None:
        if not self._active:
            return
        self._active = False
        service = self._service
        self._service = None
        if service is not None:
            try:
                service._unsubscribe(self._sid)  # noqa: SLF001 - 同一包内的句柄
            except Exception:  # noqa: BLE001 - 服务已销毁时忽略
                log.exception("取消输入订阅失败")

    def __enter__(self) -> "Subscription":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.cancel()


# ---------------------------------------------------------------------------
# 输入服务
# ---------------------------------------------------------------------------
class InputService(QObject):
    """全局输入服务：低级键盘钩子 + 手柄轮询。

    参数（都用于测试注入，正常使用全部默认即可）：
      · ``keyboard_backend``：``KeyboardBackend`` 实例；默认 Windows 低级钩子，
        非 Windows 为 Null 后端（start 失败 → error/warning，不抛）。
      · ``gamepad_factory``：``mode -> GamepadBackend``；默认 ``create_gamepad_backend``。
      · ``clock``：单调时钟（长按判定用），默认 ``time.monotonic``。
      · ``admin_check``：``() -> bool``，默认 ``is_admin``。
    """

    token = pyqtSignal(str)          # 规范化 token（仅按下/长按，Q+1 / HOLD+1 / PAD_A …）
    raw = pyqtSignal(object)         # KeyEvent（down / up / hold 全部事件）
    error = pyqtSignal(str)          # 启动失败等硬错误（可显示的中文原因）
    warning = pyqtSignal(str)        # 降级 / 非管理员 / 手柄不可用等提示
    _posted = pyqtSignal(object)     # 内部：后端线程 → 主线程的事件桥

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        keyboard_backend: KeyboardBackend | None = None,
        gamepad_factory: Callable[[str], GamepadBackend] | None = None,
        clock: Callable[[], float] | None = None,
        hold_ms: int = HOLD_MS_DEFAULT,
        gamepad: str = "off",
        admin_check: Callable[[], bool] | None = None,
    ):
        super().__init__(parent)
        self._clock = clock or time.monotonic
        self._keyboard_backend: KeyboardBackend | None = keyboard_backend
        self._gamepad_factory = gamepad_factory or create_gamepad_backend
        self._gamepad_backend: GamepadBackend | None = None
        self._gamepad_mode = normalize_gamepad_mode(gamepad)
        self._admin_check = admin_check or is_admin
        self._admin_warned = False

        self._running = False
        self._manual_start = False
        self._auto_started = False
        self._last_error: str | None = None
        self._listeners: dict[int, Callable[[str], None]] = {}
        self._next_listener_id = 0
        self._lock = threading.RLock()

        self._hold_tracker = HoldTracker(hold_ms, clock=self._clock)
        self._posted.connect(self._on_backend_event)  # AutoConnection：跨线程自动排队

        self._hold_timer = QTimer(self)
        self._hold_timer.setInterval(HOLD_CHECK_MS)
        self._hold_timer.timeout.connect(self._check_holds)

        self._gamepad_timer = QTimer(self)
        self._gamepad_timer.setInterval(GAMEPAD_POLL_MS)
        self._gamepad_timer.timeout.connect(self._poll_gamepad)

    # -- 状态 ---------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._running

    @property
    def running(self) -> bool:
        return self._running

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def hold_ms(self) -> int:
        return self._hold_tracker.hold_ms

    @property
    def gamepad_mode(self) -> str:
        return self._gamepad_mode

    @property
    def keyboard_backend(self) -> KeyboardBackend | None:
        return self._keyboard_backend

    @property
    def gamepad_backend(self) -> GamepadBackend | None:
        return self._gamepad_backend

    @property
    def listener_count(self) -> int:
        with self._lock:
            return len(self._listeners)

    def status(self) -> dict[str, Any]:
        """给设置页 / 调试看的只读状态。"""
        pad = self._gamepad_backend
        keyboard = self._keyboard_backend
        return {
            "enabled": self._running,
            "keyboard": getattr(keyboard, "name", "none") if keyboard is not None else "none",
            "keyboard_running": bool(getattr(keyboard, "running", False)) if keyboard is not None else False,
            "gamepad_mode": self._gamepad_mode,
            "gamepad": getattr(pad, "name", "none") if pad is not None else "none",
            "gamepad_connected": bool(getattr(pad, "connected", False)) if pad is not None else False,
            "hold_ms": self.hold_ms,
            "admin": self.is_admin(),
            "error": self._last_error,
            "listeners": self.listener_count,
        }

    def is_admin(self) -> bool:
        """当前进程是否管理员（结果缓存；检测本身异常时返回 False）。"""
        try:
            return bool(self._admin_check())
        except Exception:  # noqa: BLE001 - 检测失败不能影响输入服务
            log.exception("管理员检测失败")
            return False

    # -- 生命周期 -----------------------------------------------------------
    def start(self) -> bool:
        """启动键盘钩子（以及已配置的手柄轮询）。重复调用幂等，返回是否可用。"""
        self._manual_start = True
        ok = self._start_impl()
        if not ok:
            self._manual_start = False
        return ok

    def _start_impl(self) -> bool:
        if self._running:
            return True
        self._last_error = None
        if self._keyboard_backend is None:
            self._keyboard_backend = self._create_keyboard_backend()
        self._hold_tracker.reset()

        keyboard_ok = False
        try:
            self._keyboard_backend.start(self._deliver)
            keyboard_ok = True
        except Exception as exc:  # noqa: BLE001 - 后端失败 → error 信号，不抛
            message = str(exc) or f"{exc.__class__.__name__}"
            self._last_error = message
            log.warning("键盘输入启动失败：%s", message)
            self.error.emit(message)

        gamepad_ok = False
        if self._gamepad_mode != "off":
            gamepad_ok = self._start_gamepad()

        if not keyboard_ok and not gamepad_ok:
            self._running = False
            return False

        self._running = True
        self._hold_timer.start(HOLD_CHECK_MS)
        if not self.is_admin() and not self._admin_warned:
            self._admin_warned = True
            self.warning.emit(
                "当前以非管理员身份运行：如果游戏/目标窗口以管理员权限启动，"
                "Windows UIPI 会挡掉全局按键（收不到但不影响本程序窗口）。"
            )
        log.info(
            "input service started (keyboard=%s, gamepad=%s, hold_ms=%s)",
            getattr(self._keyboard_backend, "name", "?"),
            self._gamepad_mode,
            self.hold_ms,
        )
        return True

    def stop(self) -> None:
        """停止钩子 / 手柄并结束线程；重复调用幂等。"""
        self._manual_start = False
        self._auto_started = False
        was_running = self._running
        self._running = False
        try:
            self._hold_timer.stop()
            self._gamepad_timer.stop()
        except Exception:  # noqa: BLE001 - Qt 对象已销毁时不能抛
            log.exception("停止输入定时器失败")
        if not was_running:
            self._hold_tracker.reset()
            return
        backend = self._keyboard_backend
        if backend is not None:
            try:
                backend.stop()
            except Exception:  # noqa: BLE001 - 卸载钩子失败只记日志
                log.exception("停止键盘后端失败")
        self._stop_gamepad()
        self._hold_tracker.reset()
        log.info("input service stopped")

    # -- 配置 ---------------------------------------------------------------
    def set_hold_ms(self, hold_ms: object) -> int:
        """设置数字长按阈值（毫秒，夹在 80–2000）；返回生效值。"""
        value = self._hold_tracker.set_hold_ms(hold_ms)
        log.info("hold_ms = %s", value)
        return value

    def set_gamepad(self, mode: object) -> str:
        """切换手柄模式 ``off`` / ``xbox`` / ``ds``（服务运行中立即生效）。

        非法模式抛 ``ValueError``；模式不可用时发 warning 并降级为关闭。
        """
        normalized = normalize_gamepad_mode(mode)
        if normalized == self._gamepad_mode and (
            normalized == "off" or self._gamepad_backend is not None
        ):
            return normalized
        self._gamepad_mode = normalized
        if self._running:
            self._stop_gamepad()
            if normalized != "off":
                self._start_gamepad()
        return normalized

    # -- 订阅（timeline.input_adapter 等消费方使用）-------------------------
    def subscribe(self, callback: Callable[[str], Any]) -> Subscription:
        """订阅规范化 token（主线程回调）。

        首个订阅者会自动 ``start()``（方便消费方直接接入）；启动失败时抛
        ``InputBackendError``（消费方可据此回退到自己的轮询方案）。返回的句柄
        ``cancel()`` 幂等；最后一个“自动启动”的订阅取消后会自动 ``stop()``。
        """
        if not callable(callback):
            raise TypeError("subscribe 需要一个可调用对象：callback(token: str)")
        with self._lock:
            self._next_listener_id += 1
            sid = self._next_listener_id
            self._listeners[sid] = callback
        if not self._running:
            if not self._start_impl():
                with self._lock:
                    self._listeners.pop(sid, None)
                raise InputBackendError(self._last_error or "输入服务启动失败，无法订阅。")
            with self._lock:
                self._auto_started = True
        elif self._manual_start:
            with self._lock:
                self._auto_started = False
        return Subscription(self, sid)

    def add_listener(self, callback: Callable[[str], Any]) -> Subscription:
        """``subscribe`` 的别名（兼容不同消费方命名）。"""
        return self.subscribe(callback)

    def _unsubscribe(self, sid: int) -> None:
        with self._lock:
            self._listeners.pop(sid, None)
            remaining = not self._listeners
            auto = self._auto_started and not self._manual_start
        if remaining and auto:
            self.stop()

    # -- 后端创建 -----------------------------------------------------------
    def _create_keyboard_backend(self) -> KeyboardBackend:
        if _IS_WINDOWS:
            return WindowsKeyboardBackend()
        return NullKeyboardBackend()

    # -- 手柄 ---------------------------------------------------------------
    def _start_gamepad(self) -> bool:
        self._stop_gamepad()
        mode = self._gamepad_mode
        if mode == "off":
            return False
        backend: GamepadBackend | None = None
        try:
            backend = self._gamepad_factory(mode)
        except Exception as exc:  # noqa: BLE001 - 手柄失败不影响键盘
            log.exception("手柄后端初始化失败")
            self.warning.emit(f"手柄初始化失败（{mode}）：{exc}")
        if backend is None or not bool(getattr(backend, "available", False)):
            self._gamepad_backend = None
            self.warning.emit(
                f"手柄不可用（{mode}）：未检测到设备或驱动缺失，已降级为关闭。"
            )
            return False
        self._gamepad_backend = backend
        self._gamepad_timer.start(GAMEPAD_POLL_MS)
        log.info("gamepad polling started (%s/%s)", getattr(backend, "name", "?"), mode)
        return True

    def _stop_gamepad(self) -> None:
        try:
            self._gamepad_timer.stop()
        except Exception:  # noqa: BLE001
            pass
        backend = self._gamepad_backend
        self._gamepad_backend = None
        if backend is not None:
            try:
                backend.close()
            except Exception:  # noqa: BLE001
                log.exception("关闭手柄后端失败")

    def _poll_gamepad(self) -> None:
        backend = self._gamepad_backend
        if backend is None:
            self._gamepad_timer.stop()
            return
        try:
            events = backend.poll() or []
        except Exception as exc:  # noqa: BLE001 - 轮询异常不能打断 UI
            log.warning("手柄轮询失败：%s", exc)
            return
        for event in events:
            try:
                self._on_backend_event(event)
            except Exception:  # noqa: BLE001
                log.exception("手柄事件处理失败")

    # -- 事件桥 -------------------------------------------------------------
    def _deliver(self, event: KeyEvent) -> None:
        """后端回调（可能在钩子线程）：只做投递，不碰 Qt 控件。"""
        try:
            self._posted.emit(event)
        except Exception:  # noqa: BLE001 - 退出过程中队列可能已销毁
            log.exception("输入事件投递失败")

    def _on_backend_event(self, event: KeyEvent) -> None:
        """主线程：维护长按状态并发布。"""
        try:
            if event.source == "keyboard":
                self._hold_tracker.feed(event)
            self._publish(event)
        except Exception:  # noqa: BLE001
            log.exception("输入事件处理失败")

    def _publish(self, event: KeyEvent) -> None:
        self.raw.emit(event)
        if event.kind not in ("down", "hold"):
            return  # 松开不产生动作 token（raw 里仍有 up 事件）
        token = normalize_token(event.token)
        if not token:
            return
        self.token.emit(token)
        self._notify_listeners(token)

    def _notify_listeners(self, token: str) -> None:
        with self._lock:
            callbacks = list(self._listeners.values())
        for callback in callbacks:
            try:
                callback(token)
            except Exception:  # noqa: BLE001 - 单个订阅者异常不能影响其他订阅者
                log.exception("输入订阅回调异常")

    def _check_holds(self) -> None:
        if not self._running:
            self._hold_timer.stop()
            return
        try:
            for digit in self._hold_tracker.due():
                self._publish(
                    KeyEvent(
                        token=f"HOLD+{digit}",
                        kind="hold",
                        modifiers=(),
                        vk=vk_for_digit(digit),
                        timestamp=self._clock(),
                        source="keyboard",
                    )
                )
        except Exception:  # noqa: BLE001
            log.exception("长按判定失败")


# ---------------------------------------------------------------------------
# 进程级单例（可选）：timeline.input_adapter 通过 get_service() 自动接入
# ---------------------------------------------------------------------------
_singleton: InputService | None = None
_singleton_lock = threading.Lock()


def get_service() -> InputService:
    """懒创建进程级 ``InputService``（未启动；首个订阅者会自动启动）。"""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = InputService()
            log.info("input service singleton created")
        return _singleton


def subscribe(callback: Callable[[str], Any]) -> Subscription:
    """模块级便捷入口：等价于 ``get_service().subscribe(callback)``。

    `endbox.modules.timeline.input_adapter` 的自动接入会先在本包上找
    ``input_service`` / ``service`` / ``input`` 属性；由于 ``service`` 是本包的
    子模块，它会把**本模块**当成 service 对象并调用 ``subscribe``。这个函数保证
    那条接入路径也能工作（等价于订阅进程级单例）。
    """
    return get_service().subscribe(callback)


def add_listener(callback: Callable[[str], Any]) -> Subscription:
    """``subscribe`` 的别名（兼容不同消费方的命名）。"""
    return get_service().subscribe(callback)


__all__ = [
    "GAMEPAD_MODES",
    "HOLD_MS_DEFAULT",
    "HOLD_MS_MAX",
    "HOLD_MS_MIN",
    "InputService",
    "Subscription",
    "add_listener",
    "get_service",
    "is_admin",
    "subscribe",
]
