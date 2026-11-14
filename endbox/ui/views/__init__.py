# -*- coding: utf-8 -*-
"""内置视图发现：模块缺失时跳过，不阻塞主程序启动。"""
from __future__ import annotations

import importlib

from endbox.core.log import get_logger
from endbox.ui.views.base import BaseView, ViewRegistry

log = get_logger("ui.views")

# (模块名, 类名, 说明) —— 顺序即注册顺序，order 在类里定义
BUILTIN_VIEWS: list[tuple[str, str]] = [
    ("endbox.ui.views.home", "HomeView"),
    ("endbox.ui.views.account", "AccountView"),
    ("endbox.ui.views.timeline", "TimelineView"),
    ("endbox.ui.views.hud", "HudView"),
    ("endbox.ui.views.calc325", "Calc325View"),
    ("endbox.ui.views.plugins", "PluginManagerView"),
    ("endbox.ui.views.settings", "SettingsView"),
]


def load_views(registry: ViewRegistry) -> dict[str, str]:
    """返回 {模块名: 错误信息}，成功的模块注册进 registry。"""
    errors: dict[str, str] = {}
    for module_name, class_name in BUILTIN_VIEWS:
        try:
            module = importlib.import_module(module_name)
            view_cls = getattr(module, class_name)
            if not (isinstance(view_cls, type) and issubclass(view_cls, BaseView)):
                raise TypeError(f"{class_name} is not a BaseView")
            registry.register(view_cls)
        except ModuleNotFoundError as exc:
            # 模块还没实现（开发期）不是错误
            log.info("view module missing: %s (%s)", module_name, exc)
            errors[module_name] = f"missing: {exc}"
        except Exception as exc:  # noqa: BLE001
            log.exception("view module failed: %s", module_name)
            errors[module_name] = str(exc)
    return errors
