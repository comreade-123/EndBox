# -*- coding: utf-8 -*-
"""模块服务容器：各功能模块把自己的服务挂在这里，其他模块通过它访问。

内置模块与插件之间默认隔离；插件只能通过 PluginContext 里白名单 API 访问，
见 endbox/plugins/host.py。
"""
from __future__ import annotations

from typing import Any


class Services:
    """惰性服务表。属性由各模块在初始化时赋值。"""

    account: Any = None
    timeline: Any = None
    hud: Any = None
    plugins: Any = None

    def get(self, name: str) -> Any:
        return getattr(self, name, None)

    def require(self, name: str) -> Any:
        service = getattr(self, name, None)
        if service is None:
            raise RuntimeError(f"service {name!r} is not available")
        return service

    def snapshot(self) -> dict[str, bool]:
        return {
            "account": self.account is not None,
            "timeline": self.timeline is not None,
            "hud": self.hud is not None,
            "plugins": self.plugins is not None,
        }


services = Services()
