# -*- coding: utf-8 -*-
"""HUD 模块（覆盖层）。

对主程序只暴露 :func:`create_service`：``endbox.app._init_services`` 导入本包并调用，
得到一个挂在 ``ctx.services.hud`` 上的 :class:`HudService`。
视图 ``endbox/ui/views/hud.py`` 通过 ``ctx.services.hud`` 使用它。

子模块：
  service.py  服务：窗口生命周期、热键、事件聚合
  overlay.py  覆盖层窗口 + 画布 + 三段式动画
  hotkey.py   RegisterHotKey + 原生事件过滤
  battery.py  GetSystemPowerStatus（ctypes）
  state.py    纯函数：真实数据 → 显示文案
"""
from __future__ import annotations

from endbox.app_context import AppContext
from endbox.modules.hud.service import HudService

__all__ = ["HudService", "create_service"]


def create_service(ctx: AppContext) -> HudService:
    """模块工厂（契约要求）。"""
    return HudService(ctx)
