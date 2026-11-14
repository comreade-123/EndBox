# -*- coding: utf-8 -*-
"""电池读取：ctypes 调用 Win32 ``GetSystemPowerStatus``。

只读取系统真实数据；非 Windows、接口失败、没有电池（台式机）时返回
``available=False``，界面显示 ``—``，绝不编造百分比。

``read_power()`` 的返回结构::

    {
      "available": bool,      # 是否读到了系统状态
      "percent": int | None,  # 0–100；系统报告未知或没有电池时为 None
      "ac": bool | None,      # True=外接电源，False=电池供电，None=未知
      "charging": bool | None,
      "remaining_s": int | None,  # 估算剩余秒数；未知 / 交流供电时为 None
      "error": str | None,
    }
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from endbox.core.log import get_logger
from endbox.core.text import t

log = get_logger("hud.battery")

BATTERY_FLAG_CHARGING = 0x08
BATTERY_FLAG_NO_BATTERY = 0x80
BATTERY_FLAG_UNKNOWN = 0xFF
UNKNOWN_PERCENT = 255
UNKNOWN_AC = 255


class PowerStatus(ctypes.Structure):
    """Win32 ``SYSTEM_POWER_STATUS``。"""

    _fields_ = [
        ("ACLineStatus", wintypes.BYTE),
        ("BatteryFlag", wintypes.BYTE),
        ("BatteryLifePercent", wintypes.BYTE),
        ("SystemStatusFlag", wintypes.BYTE),
        ("BatteryLifeTime", wintypes.DWORD),
        ("BatteryFullLifeTime", wintypes.DWORD),
    ]


def _read_status() -> PowerStatus:
    """调用 ``GetSystemPowerStatus``；失败抛异常（测试里替换这个函数）。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetSystemPowerStatus.argtypes = [ctypes.POINTER(PowerStatus)]
    kernel32.GetSystemPowerStatus.restype = wintypes.BOOL
    status = PowerStatus()
    if not kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        raise ctypes.WinError(ctypes.get_last_error())
    return status


def empty_power(error: str | None = None) -> dict:
    """不可用状态（结构固定，调用方不用判空）。"""
    return {
        "available": False,
        "percent": None,
        "ac": None,
        "charging": None,
        "remaining_s": None,
        "error": error,
    }


def read_power() -> dict:
    """读取一次电池 / 供电状态。任何失败都降级为 ``available=False``。"""
    if not sys.platform.startswith("win"):
        return empty_power(t("hud.errors.battery_windows_only"))
    try:
        status = _read_status()
    except Exception as exc:  # noqa: BLE001 - 读取失败必须降级，不能影响 HUD
        log.warning("battery read failed: %s", exc)
        return empty_power(str(exc) or exc.__class__.__name__)

    result = empty_power()
    result["available"] = True

    percent = int(status.BatteryLifePercent)
    result["percent"] = None if percent == UNKNOWN_PERCENT else max(0, min(100, percent))

    ac = int(status.ACLineStatus)
    result["ac"] = None if ac == UNKNOWN_AC else bool(ac)

    flags = int(status.BatteryFlag)
    if flags == BATTERY_FLAG_UNKNOWN:
        result["charging"] = None
    elif flags & BATTERY_FLAG_NO_BATTERY:
        result["charging"] = False
    else:
        result["charging"] = bool(flags & BATTERY_FLAG_CHARGING)

    # 剩余时间：Windows 报告 0xFFFFFFFF（未知）或 0（正在计算 / 交流供电）时按"未知"处理
    life = int(status.BatteryLifeTime)
    result["remaining_s"] = None if life in (0, 0xFFFFFFFF) else max(0, life)
    return result
