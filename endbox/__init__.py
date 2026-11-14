# -*- coding: utf-8 -*-
"""EndBox · 终末地游戏工具箱。

一个基于 PyQt6 的桌面工具箱：账号中心、技能轴排轴、HUD 覆盖层、325 计算器，
以及可扩展的插件宿主。
"""

try:  # 唯一版本源由 endbox/version.py 提供；模块尚未就绪时退回兼容值
    from endbox.version import __version__  # noqa: F401
except Exception:  # noqa: BLE001 - 版本模块缺失不应阻塞导入
    __version__ = "1.1.0"
__app_name__ = "EndBox"
__author__ = "EthanMaven、DeepSeek-V4.1-Flash"
APP_CODE = "ENDBOX"
