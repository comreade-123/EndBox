# -*- coding: utf-8 -*-
"""HUD 显示状态的纯函数：真实数据 → 界面文案，以及"内容没变就不重画"的键。

HUD 只负责 **账号 / 电池 / 时钟**（以及标题里的 Windows 用户名）。
排轴运行态由排轴模块自己的提示条负责，两者互相独立：这里不读、不订阅排轴数据。

原则：没有数据就写 ``未连接`` / ``—``，绝不编造。
"""
from __future__ import annotations

import getpass
import sys
from datetime import datetime

from endbox.core.text import t

MISSING = t("common.placeholder")
DISCONNECTED = t("hud.account.disconnected")

ANCHORS = ("top-center", "top-left", "top-right")
ANCHOR_LABELS = {anchor: t(f"hud.anchors.{anchor}") for anchor in ANCHORS}
MODES = ("compact", "standard")
MODE_LABELS = {mode: t(f"hud.modes.{mode}") for mode in MODES}

SCALE_MIN, SCALE_MAX, SCALE_DEFAULT = 0.6, 1.6, 1.0
OPACITY_MIN, OPACITY_MAX, OPACITY_DEFAULT = 0.30, 1.0, 0.96
OFFSET_X_MIN, OFFSET_X_MAX = -800, 800
OFFSET_Y_MIN, OFFSET_Y_MAX = -400, 800
BATTERY_TICKS = 10

# 显示项开关（短名 → 设置路径由 service 负责映射）
DEFAULT_SHOW = {"account": True, "battery": True, "clock": True}
SHOW_LABELS = {
    "account": t("hud.display.item_account"),
    "battery": t("hud.display.item_battery"),
    "clock": t("hud.display.item_clock"),
}


# ---------------------------------------------------------------------------
# 数值与选项
# ---------------------------------------------------------------------------

def _clamp(value, low, high, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(low, min(high, number))


def _num(value):
    """只接受真正的数字（bool 不算）。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def clamp_scale(value) -> float:
    return round(_clamp(value, SCALE_MIN, SCALE_MAX, SCALE_DEFAULT), 2)


def clamp_opacity(value) -> float:
    return round(_clamp(value, OPACITY_MIN, OPACITY_MAX, OPACITY_DEFAULT), 2)


def clamp_offset(value, axis: str = "x") -> int:
    if axis == "y":
        low, high = OFFSET_Y_MIN, OFFSET_Y_MAX
    else:
        low, high = OFFSET_X_MIN, OFFSET_X_MAX
    return int(round(_clamp(value, low, high, 0)))


def normalize_anchor(anchor) -> str:
    text = str(anchor or "")
    return text if text in ANCHORS else ANCHORS[0]


def anchor_index(anchor) -> int:
    return ANCHORS.index(normalize_anchor(anchor))


def normalize_mode(mode) -> str:
    text = str(mode or "")
    return text if text in MODES else MODES[0]


def mode_index(mode) -> int:
    return MODES.index(normalize_mode(mode))


# ---------------------------------------------------------------------------
# 格式化
# ---------------------------------------------------------------------------

def fmt_percent(value) -> str:
    return f"{int(round(float(value)))}%"


def fmt_clock(moment: datetime | None = None, use_12h: bool = False, show_seconds: bool = True) -> str:
    now = moment or datetime.now()
    if use_12h:
        hour = now.hour % 12 or 12
        text = f"{hour}:{now.minute:02d}"
        if show_seconds:
            text += f":{now.second:02d}"
        return text + (" AM" if now.hour < 12 else " PM")
    text = f"{now.hour:02d}:{now.minute:02d}"
    if show_seconds:
        text += f":{now.second:02d}"
    return text


def fmt_battery_remaining(seconds) -> str:
    """``BatteryLifeTime``（秒）→ ``"2h05m"`` / ``"45m"``；未知 → ``—``。"""
    number = _num(seconds)
    if number is None or number < 0:
        return MISSING
    minutes = int(round(number / 60.0))
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


# ---------------------------------------------------------------------------
# 当前 Windows 用户名（HUD 标题）
# ---------------------------------------------------------------------------

def _username_env() -> str:
    return getpass.getuser()


def _username_win() -> str:
    if not sys.platform.startswith("win"):
        return ""
    import ctypes
    from ctypes import wintypes

    size = wintypes.DWORD(256)
    buffer = ctypes.create_unicode_buffer(256)
    if ctypes.windll.advapi32.GetUserNameW(buffer, ctypes.byref(size)):
        return buffer.value
    return ""


def current_username() -> str:
    """Windows 登录用户名；两个来源都取不到时返回 ``—``。"""
    for provider in (_username_env, _username_win):
        try:
            name = provider()
        except Exception:  # noqa: BLE001 - 取不到用户名不是错误
            name = None
        text = str(name or "").strip()
        if text:
            return text
    return MISSING


# ---------------------------------------------------------------------------
# 默认（未连接）结构
# ---------------------------------------------------------------------------

def default_account() -> dict:
    return {
        "connected": False,
        "nickname": None,
        "online": None,
        "provider": "",
        "avatar_path": None,
        "last_sync": 0,
        "error": None,
    }


# ---------------------------------------------------------------------------
# 视图数据
# ---------------------------------------------------------------------------

def account_view(account: dict | None) -> dict:
    raw = account if isinstance(account, dict) else {}
    connected = bool(raw.get("connected"))
    nickname = str(raw.get("nickname") or "").strip()
    online = raw.get("online") if isinstance(raw.get("online"), bool) else None
    avatar = str(raw.get("avatar_path") or "").strip() or None
    if not connected:
        return {"connected": False, "online": None, "text": DISCONNECTED, "flag": True, "avatar": None}
    return {"connected": True, "online": online, "text": nickname or MISSING, "flag": False, "avatar": avatar}


def battery_view(power: dict | None) -> dict:
    """电池状态 → 百分比 + 刻度 + 供电方式 + 剩余时间（真实读数）。"""
    raw = power if isinstance(power, dict) else {}
    available = bool(raw.get("available"))
    percent = raw.get("percent")
    if isinstance(percent, int) and not isinstance(percent, bool) and 0 <= percent <= 100 and available:
        percent_text = fmt_percent(percent)
        ticks = max(0, min(BATTERY_TICKS, int(round(percent / 100 * BATTERY_TICKS))))
        low = percent <= 20
    else:
        percent = None
        percent_text = MISSING
        ticks = None
        low = False
    ac = raw.get("ac") if isinstance(raw.get("ac"), bool) else None
    charging = raw.get("charging") if isinstance(raw.get("charging"), bool) else None
    if charging:
        badge = "CHG"
        mode_text = t("hud.battery.mode_charging")
    elif ac is True:
        badge = "AC"
        mode_text = t("hud.battery.mode_ac")
    elif ac is False:
        badge = ""
        mode_text = t("hud.battery.mode_battery")
    else:
        badge = ""
        mode_text = MISSING
    remaining = fmt_battery_remaining(raw.get("remaining_s"))
    if mode_text == MISSING:
        detail_text = MISSING
    elif remaining == MISSING:
        detail_text = mode_text
    else:
        detail_text = t("hud.battery.detail", mode=mode_text, remaining=remaining)
    return {
        "available": available,
        "percent": percent,
        "percent_text": percent_text,
        "ticks": ticks,
        "low": low,
        "badge": badge,
        "mode_text": mode_text,
        "remaining_text": remaining,
        "detail_text": detail_text,
    }


def clock_view(clock: str | None) -> dict:
    text = str(clock or "").strip()
    return {"text": text or MISSING}


# ---------------------------------------------------------------------------
# 重绘 / 广播去重键
# ---------------------------------------------------------------------------

def render_key(snapshot: dict | None) -> tuple:
    """画布内容指纹：这些值都没变时不需要重画（防闪烁的关键）。"""
    raw = snapshot if isinstance(snapshot, dict) else {}
    show = raw.get("show") or {}
    av = account_view(raw.get("account"))
    bv = battery_view(raw.get("battery"))
    return (
        normalize_mode(raw.get("mode")),
        tuple(sorted((str(key), bool(value)) for key, value in show.items())),
        str(raw.get("username") or ""),
        av["connected"],
        av["text"],
        av["online"],
        av["avatar"],
        bv["percent_text"],
        bv["ticks"],
        bv["low"],
        bv["badge"],
        bv["mode_text"],
        bv["remaining_text"],
        bv["detail_text"],
        str(raw.get("clock") or ""),
    )


def snapshot_key(snapshot: dict | None) -> tuple:
    """完整快照指纹：服务广播与控制页刷新也只在真的变化时发生。"""
    raw = snapshot if isinstance(snapshot, dict) else {}
    hotkey = raw.get("hotkey") or {}
    return (
        render_key(raw),
        bool(raw.get("enabled")),
        bool(raw.get("window_created")),
        bool(raw.get("window_visible")),
        str(hotkey.get("spec") or ""),
        bool(hotkey.get("registered")),
        str(hotkey.get("error") or ""),
        normalize_anchor(raw.get("anchor")),
        clamp_offset(raw.get("offset_x"), "x"),
        clamp_offset(raw.get("offset_y"), "y"),
        str(raw.get("screen") or ""),
        clamp_scale(raw.get("scale")),
        clamp_opacity(raw.get("opacity")),
        bool(raw.get("clock_12h")),
        bool(raw.get("clock_seconds")),
    )
