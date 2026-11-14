# -*- coding: utf-8 -*-
"""管理员权限检测与"以管理员身份重启"。

排轴运行态需要全局读取游戏按键：游戏若以管理员权限运行，非管理员的 EndBox
会被 Windows UIPI 挡掉按键（钩子收不到）。这里提供检测与一键提权重启。
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

from endbox.core.log import get_logger
from endbox.core.text import t

log = get_logger("core.elevation")

_IS_WIN = sys.platform.startswith("win")


def is_admin() -> bool:
    """当前进程是否以管理员权限运行（非 Windows 恒为 False）。"""
    if not _IS_WIN:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        log.exception("管理员权限检测失败")
        return False


def _launch_targets() -> tuple[str, str]:
    """返回 (可执行文件, 参数)。源码运行时是 python.exe + main.py。"""
    if getattr(sys, "frozen", False):
        return sys.executable, " ".join(sys.argv[1:])
    exe = sys.executable
    main_py = Path(__file__).resolve().parents[2] / "main.py"
    return exe, f'"{main_py}" ' + " ".join(sys.argv[1:])


def relaunch_as_admin() -> bool:
    """用 UAC 以管理员身份重启当前程序；成功发起返回 True（当前进程应尽快退出）。"""
    if not _IS_WIN:
        return False
    try:
        exe, params = _launch_targets()
        # SW_SHOWNORMAL = 1
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        if int(result) > 32:
            log.info("elevated relaunch requested: %s %s", exe, params)
            return True
        log.warning("elevated relaunch rejected (ShellExecute -> %s)", result)
    except Exception:  # noqa: BLE001
        log.exception("elevated relaunch failed")
    return False


def admin_hint() -> str:
    """给界面用的一句话说明。"""
    if is_admin():
        return t("shell.elevation.admin_ok")
    return t("shell.elevation.admin_hint")
