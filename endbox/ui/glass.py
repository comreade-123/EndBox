# -*- coding: utf-8 -*-
"""毛玻璃与窗口特效。

EndBox 用 **系统级实时模糊**（DWM 合成）做毛玻璃：窗口透明区域由 Windows 实时合成
桌面模糊，拖动、换壁纸都即时生效，不截图、不需要「刷新背景」这类按钮。

- Windows 11 22H2+：``DWMWA_SYSTEMBACKDROP_TYPE = DWMSBT_TRANSIENTWINDOW``（Acrylic）
- 更老的系统：退回 ``SetWindowCompositionAttribute`` 的 blur-behind（同样实时）
- 都不支持时（Linux / 关闭毛玻璃的截图环境）：窗口用纯色底，不影响使用

HUD 的鼠标穿透也在这里。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from endbox.core.log import get_logger

log = get_logger("ui.glass")

_IS_WIN = sys.platform.startswith("win")

ACCENT_DISABLED = 0
ACCENT_ENABLE_BLURBEHIND = 3
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
WCA_ACCENT_POLICY = 19
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_SYSTEMBACKDROP_TYPE = 38

DWMSBT_NONE = 1
DWMSBT_MAINWINDOW = 2        # Mica
DWMSBT_TRANSIENTWINDOW = 3   # Acrylic（实时模糊，适合工具窗口）
DWMSBT_TABBEDWINDOW = 4
DWMWCP_ROUND = 2


def set_rounded_corners(window, preference: int = DWMWCP_ROUND) -> bool:
    """让 DWM 自己把窗口四角画圆（accent 模糊会铺满整个窗口矩形，要靠系统圆角裁）。"""
    if not _IS_WIN:
        return False
    hwnd = _hwnd(window)
    if not hwnd:
        return False
    return _dwm_set_int(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, int(preference))


class _AccentPolicy(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_uint),
        ("AccentFlags", ctypes.c_uint),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", ctypes.c_uint),
    ]


class _WindowCompositionAttributeData(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_uint),
        ("Data", ctypes.c_void_p),
        ("SizeOfData", ctypes.c_size_t),
    ]


def _tint_argb(hex_color: str, alpha: int = 0xCC) -> int:
    """#RRGGBB + alpha → 0xAABBGGRR（SetWindowCompositionAttribute 用 BGRA）。"""
    value = hex_color.lstrip("#")
    if len(value) == 8:
        a, r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4, 6))
    else:
        r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
        a = alpha
    return (a << 24) | (r << 16) | (g << 8) | b


def _hwnd(widget) -> int:
    try:
        return int(widget.winId())
    except (AttributeError, RuntimeError, TypeError):
        return 0


def _dwm_set_int(hwnd: int, attribute: int, value: int) -> bool:
    try:
        dwm = ctypes.WinDLL("dwmapi")
        data = ctypes.c_int(int(value))
        result = dwm.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), ctypes.c_uint(attribute), ctypes.byref(data), ctypes.sizeof(data)
        )
        return result == 0
    except Exception:  # noqa: BLE001
        log.debug("dwm attribute %s failed", attribute, exc_info=True)
        return False


def _accent_blur(window) -> bool:
    """Windows 10 的 blur-behind：实时模糊，不需要截图。"""
    hwnd = _hwnd(window)
    if not hwnd:
        return False
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SetWindowCompositionAttribute.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(_WindowCompositionAttributeData),
        ]
        user32.SetWindowCompositionAttribute.restype = wintypes.BOOL
        policy = _AccentPolicy(ACCENT_ENABLE_BLURBEHIND, 2, 0x00000000, 0)
        data = _WindowCompositionAttributeData(
            WCA_ACCENT_POLICY,
            ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p),
            ctypes.sizeof(policy),
        )
        return bool(user32.SetWindowCompositionAttribute(wintypes.HWND(hwnd), ctypes.byref(data)))
    except Exception:  # noqa: BLE001
        log.exception("accent blur failed")
        return False


def apply_acrylic(window, *, tint: str = "#161616", alpha: int = 0xCC) -> bool:
    """额外把窗口染成指定颜色（可选增强，不影响实时模糊）。"""
    if not _IS_WIN:
        return False
    hwnd = _hwnd(window)
    if not hwnd:
        return False
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SetWindowCompositionAttribute.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(_WindowCompositionAttributeData),
        ]
        user32.SetWindowCompositionAttribute.restype = wintypes.BOOL
        policy = _AccentPolicy(ACCENT_ENABLE_ACRYLICBLURBEHIND, 2, _tint_argb(tint, alpha), 0)
        data = _WindowCompositionAttributeData(
            WCA_ACCENT_POLICY,
            ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p),
            ctypes.sizeof(policy),
        )
        return bool(user32.SetWindowCompositionAttribute(wintypes.HWND(hwnd), ctypes.byref(data)))
    except Exception:  # noqa: BLE001
        log.exception("acrylic failed")
        return False


def disable_acrylic(window) -> None:
    if not _IS_WIN:
        return
    hwnd = _hwnd(window)
    if not hwnd:
        return
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SetWindowCompositionAttribute.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(_WindowCompositionAttributeData),
        ]
        policy = _AccentPolicy(ACCENT_DISABLED, 0, 0, 0)
        data = _WindowCompositionAttributeData(
            WCA_ACCENT_POLICY,
            ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p),
            ctypes.sizeof(policy),
        )
        user32.SetWindowCompositionAttribute(wintypes.HWND(hwnd), ctypes.byref(data))
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 实时毛玻璃：DWM 合成桌面模糊（不截图，不需要手动刷新）
# ---------------------------------------------------------------------------
def enable_realtime_glass(window, *, dark: bool = True) -> bool:
    """开启系统实时模糊。成功返回 True；失败时窗口退回纯色底。

    优先用 ``SetWindowCompositionAttribute`` 的 blur-behind（对无边框 / 分层窗口最稳，
    桌面内容实时模糊），再尝试 Win11 的系统背景材质。
    """
    if not _IS_WIN:
        return False
    hwnd = _hwnd(window)
    if not hwnd:
        return False
    _dwm_set_int(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0)
    if _accent_blur(window):
        return True
    if _dwm_set_int(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_TRANSIENTWINDOW):
        return True
    return False


class _BlurBehind(ctypes.Structure):
    _fields_ = [
        ("dwFlags", wintypes.DWORD),
        ("fEnable", wintypes.BOOL),
        ("hRgnBlur", wintypes.HANDLE),
        ("fTransitionOnMaximized", wintypes.BOOL),
    ]


_DWM_BB_ENABLE = 0x00000001


def set_blur_behind(window, enabled: bool = True) -> bool:
    """系统实时模糊（DwmEnableBlurBehindWindow）：DWM 合成桌面并模糊，窗口移动不用重抓。

    实测比 Mica / Acrylic 更可靠：只要窗口本身不加不透明底色，桌面会被实时高斯模糊。
    """
    if not _IS_WIN:
        return False
    hwnd = _hwnd(window)
    if not hwnd:
        return False
    try:
        dwm = ctypes.WinDLL("dwmapi")
        data = _BlurBehind(_DWM_BB_ENABLE, bool(enabled), None, False)
        result = dwm.DwmEnableBlurBehindWindow(wintypes.HWND(hwnd), ctypes.byref(data))
        return int(result) == 0
    except Exception:  # noqa: BLE001
        log.exception("blur-behind failed")
        return False


def disable_realtime_glass(window) -> None:
    """关掉实时模糊（最大化 / 关闭毛玻璃时用）。"""
    if not _IS_WIN:
        return
    hwnd = _hwnd(window)
    if not hwnd:
        return
    _dwm_set_int(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_NONE)
    disable_acrylic(window)


def set_dark_titlebar(window, dark: bool) -> None:
    if not _IS_WIN:
        return
    hwnd = _hwnd(window)
    if not hwnd:
        return
    _dwm_set_int(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0)


def set_click_through(window, enabled: bool = True) -> bool:
    """鼠标穿透（HUD 用）：WS_EX_LAYERED | WS_EX_TRANSPARENT。"""
    if not _IS_WIN:
        return False
    hwnd = _hwnd(window)
    if not hwnd:
        return False
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_NOACTIVATE = 0x08000000
        get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        get_long.restype = ctypes.c_longlong
        set_long.restype = ctypes.c_longlong
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
        style = get_long(wintypes.HWND(hwnd), GWL_EXSTYLE)
        new_style = style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        new_style = (new_style | WS_EX_LAYERED | WS_EX_TRANSPARENT) if enabled else (
            new_style & ~WS_EX_TRANSPARENT
        )
        set_long(wintypes.HWND(hwnd), GWL_EXSTYLE, new_style)
        return True
    except Exception:  # noqa: BLE001
        log.exception("click-through failed")
        return False



# ---------------------------------------------------------------------------
# 按需抓屏毛玻璃：临时 cloak 窗口 → 抓窗口背后的桌面 → 恢复
# ---------------------------------------------------------------------------
DWMWA_CLOAK = 13


def set_cloaked(window, cloaked: bool) -> bool:
    """把窗口从 DWM 合成里临时隐藏（不改变可见状态，可立即恢复）。"""
    hwnd = _hwnd(window)
    if not hwnd:
        return False
    return _dwm_set_int(hwnd, DWMWA_CLOAK, 1 if cloaked else 0)


class GlassField:
    """低分辨率模糊底图：窗口移动时按偏移裁一块放大（每帧 1–3ms）。"""

    DIVISOR = 8

    def __init__(self, image, origin, logical_size):
        from PyQt6.QtCore import QPoint, QSize

        self._image = image
        self._origin = QPoint(origin)
        self._logical = QSize(logical_size)
        self._cache_key = None
        self._cache_pix = None

    @classmethod
    def from_pixmap(cls, pixmap, origin, logical_size, divisor: int = 0):
        from PyQt6.QtCore import QSize, Qt

        if pixmap is None or pixmap.isNull():
            return None
        image = pixmap.toImage()
        image.setDevicePixelRatio(1.0)
        logical = QSize(logical_size)
        if image.size() != logical:
            image = image.scaled(
                logical,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        div = divisor or cls.DIVISOR
        small = image.scaled(
            max(2, int(logical.width()) // div),
            max(2, int(logical.height()) // div),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        return cls(small, origin, logical)

    def frame(self, geo, size):
        from PyQt6.QtCore import QRectF, Qt
        from PyQt6.QtGui import QPixmap

        if self._image is None or self._image.isNull():
            return None
        key = (
            int(geo.x()), int(geo.y()), int(geo.width()), int(geo.height()),
            int(size.width()), int(size.height()),
        )
        if key == self._cache_key and self._cache_pix is not None:
            return self._cache_pix
        scale_x = self._image.width() / max(1.0, float(self._logical.width()))
        scale_y = self._image.height() / max(1.0, float(self._logical.height()))
        source = QRectF(
            (geo.x() - self._origin.x()) * scale_x,
            (geo.y() - self._origin.y()) * scale_y,
            max(1.0, geo.width() * scale_x),
            max(1.0, geo.height() * scale_y),
        )
        bounds = QRectF(0, 0, self._image.width(), self._image.height())
        if not source.intersects(bounds):
            return None
        visible = source.intersected(bounds)
        if visible.width() < 1 or visible.height() < 1:
            return None
        crop = self._image.copy(visible.toAlignedRect().intersected(self._image.rect()))
        if crop.isNull():
            return None
        pixmap = QPixmap.fromImage(
            crop.scaled(
                int(size.width()),
                int(size.height()),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self._cache_key = key
        self._cache_pix = pixmap
        return pixmap
