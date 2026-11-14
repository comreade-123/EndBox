# -*- coding: utf-8 -*-
"""毛玻璃与窗口特效。

Windows 11 的 ``ACCENT_ENABLE_ACRYLICBLURBEHIND`` 在部分版本上只对客户区做
平涂染色、不再模糊背景（实测 26200），所以 EndBox 采用**自绘毛玻璃**：
启动 / 移动窗口后截取窗口背后的桌面，做降采样模糊 + 纸色染色，铺在窗口底层；
侧边栏顶部再叠半透明表面。结果稳定可控，且不依赖 DWM 的版本行为。
原生 acrylic 仍保留为可选增强（见 :func:`apply_acrylic`），HUD 鼠标穿透也在这里。
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
    except (RuntimeError, TypeError):
        return 0


def apply_acrylic(window, *, tint: str = "#161616", alpha: int = 0xCC) -> bool:
    """给无边框窗口加毛玻璃。成功返回 True。"""
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
        ok = bool(user32.SetWindowCompositionAttribute(wintypes.HWND(hwnd), ctypes.byref(data)))
        if ok:
            _win11_extras(hwnd)
        return ok
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


def _win11_extras(hwnd: int) -> None:
    try:
        dwm = ctypes.WinDLL("dwmapi")
        value = ctypes.c_int(2)  # DWMWCP_ROUND? 用 2 = 圆角；EndBox 要直角，改用 1（不圆角）
        dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(value), 4)
    except Exception:  # noqa: BLE001
        pass


def set_dark_titlebar(window, dark: bool) -> None:
    if not _IS_WIN:
        return
    hwnd = _hwnd(window)
    if not hwnd:
        return
    try:
        dwm = ctypes.WinDLL("dwmapi")
        value = ctypes.c_int(1 if dark else 0)
        dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), 4)
    except Exception:  # noqa: BLE001
        pass


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
# 自绘毛玻璃：抓取窗口背后的桌面 → 模糊 → 染色
# ---------------------------------------------------------------------------
def grab_desktop(screen, rect) -> "QPixmap | None":
    """抓取屏幕上的 rect（逻辑像素坐标），失败返回 None。"""
    if screen is None:
        return None
    try:
        from PyQt6.QtGui import QPixmap

        pix = screen.grabWindow(
            0, int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height())
        )
        if pix.isNull() or pix.width() <= 1:
            return None
        return pix
    except Exception:  # noqa: BLE001
        log.exception("grab_desktop failed")
        return None


def frosted(pix, size, passes: int = 3):
    """降采样 → 多次平滑放大，得到廉价且稳定的毛玻璃底图。"""
    from PyQt6.QtCore import QSize, Qt

    if pix is None or pix.isNull():
        return None
    small_w = max(2, int(size.width()) // 12)
    small_h = max(2, int(size.height()) // 12)
    small = pix.scaled(
        small_w, small_h,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    for _ in range(max(0, passes - 1)):
        small = small.scaled(
            max(2, small.width() // 2), max(2, small.height() // 2),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return small.scaled(
        int(size.width()), int(size.height()),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


# ---------------------------------------------------------------------------
# 实时毛玻璃：整屏抓一次 → 低分辨率「模糊场」→ 窗口移动时按偏移裁剪
# ---------------------------------------------------------------------------
class GlassField:
    """一张覆盖所有屏幕的低分辨率模糊底图。

    窗口移动/缩放时不再重新截屏（那会闪一下），只从这张小图里裁出窗口位置对应的一块
    再平滑放大 —— 背景会跟着窗口真实"走"，每帧只裁一次小图，开销约 1–2ms。

    - ``capture(screens)``：启动时抓一次整屏并降采样；
    - ``frame(geo, size)``：按窗口逻辑几何裁一块，返回 ``QPixmap``；超出抓图范围返回 None。
    """

    DIVISOR = 12          # 每个方向 /12：1320 宽的窗口 ≈ 110px 的源，放大后就是毛玻璃
    EXTRA_BLUR = 1        # 再折半一次，柔化色块边界

    def __init__(self, image, origin, logical_size):
        from PyQt6.QtCore import QPoint, QSize

        self._image = image                     # QImage，低分辨率
        self._origin = QPoint(origin)           # 抓图区域左上角的逻辑坐标
        self._logical = QSize(logical_size)     # 抓图区域的逻辑尺寸

    # -- 抓图 ---------------------------------------------------------------
    @classmethod
    def capture(cls, screens):
        """抓取所有屏幕拼成一张图，降采样成模糊场。失败返回 None。"""
        from PyQt6.QtCore import QPoint, QRect, QSize, Qt
        from PyQt6.QtGui import QImage, QPainter

        screens = [screen for screen in (screens or []) if screen is not None]
        if not screens:
            return None
        union = QRect(screens[0].geometry())
        for screen in screens[1:]:
            union = union.united(screen.geometry())
        if union.width() < 64 or union.height() < 64:
            return None

        canvas = QImage(union.size(), QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.GlobalColor.transparent)
        painted = False
        for screen in screens:
            geo = screen.geometry()
            try:
                pix = screen.grabWindow(0, 0, 0, geo.width(), geo.height())
            except Exception:  # noqa: BLE001 - 抓图失败时其他屏幕照常
                log.exception("glass grab failed")
                continue
            if pix is None or pix.isNull():
                continue
            image = pix.toImage()
            image.setDevicePixelRatio(1.0)
            if image.size() != geo.size():
                image = image.scaled(
                    geo.size(),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            painter = QPainter(canvas)
            painter.drawImage(geo.topLeft() - union.topLeft(), image)
            painter.end()
            painted = True
        if not painted:
            return None

        small = canvas.scaled(
            max(2, int(union.width()) // cls.DIVISOR),
            max(2, int(union.height()) // cls.DIVISOR),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        for _ in range(max(0, cls.EXTRA_BLUR)):
            small = small.scaled(
                max(2, small.width() // 2),
                max(2, small.height() // 2),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        return cls(small, union.topLeft(), QSize(union.size()))

    # -- 取帧 ---------------------------------------------------------------
    def frame(self, geo, size):
        """窗口几何（逻辑像素）→ 该位置对应的毛玻璃图；完全在图外返回 None。"""
        from PyQt6.QtCore import QRect, QRectF, Qt
        from PyQt6.QtGui import QPixmap

        if self._image is None or self._image.isNull():
            return None
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
        # 窗口只探出抓图区域一部分时，先裁可见部分再拉伸，避免黑边
        visible = source.intersected(bounds)
        if visible.width() < 1 or visible.height() < 1:
            return None
        crop = self._image.copy(visible.toAlignedRect().intersected(self._image.rect()))
        if crop.isNull():
            return None
        return QPixmap.fromImage(
            crop.scaled(
                int(size.width()),
                int(size.height()),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
