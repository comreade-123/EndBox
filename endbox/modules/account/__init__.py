# -*- coding: utf-8 -*-
"""账号中心模块（官服森空岛 Token + B 服扫码）。

``endbox/app.py`` 通过 ``create_service(ctx)`` 装配；视图通过
``ctx.services.account`` 访问。服务缺失是合法状态，视图必须能打开。
"""
from __future__ import annotations

from endbox.modules.account.service import AccountService, create_service

__all__ = ["AccountService", "create_service"]
