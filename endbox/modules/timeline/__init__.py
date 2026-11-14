# -*- coding: utf-8 -*-
"""排轴模块（模块二，v3）：有序步骤序列 + 干员素材/详情 + 轴库/编队库 + 分享码 + 按键提示式运行态。"""
from __future__ import annotations

from endbox.modules.timeline.assets import AssetLibrary, Operator, OperatorExtra
from endbox.modules.timeline.library import AxisLibrary
from endbox.modules.timeline.model import (
    BLOCK_KINDS,
    DEFAULT_LOOP_COUNT,
    KIND_LABELS,
    KIND_ORDER,
    KINDS,
    MARKER_KINDS,
    SCHEMA_VERSION,
    SLOT_COUNT,
    Step,
    Team,
    TimelineModel,
    normalize_loop_count,
)
from endbox.modules.timeline import share
from endbox.modules.timeline.service import TimelineService
from endbox.core.text import t as tr


def create_service(ctx):
    """``endbox.app._init_services`` 调用的工厂。"""
    return TimelineService(ctx)


__all__ = [
    "AssetLibrary",
    "AxisLibrary",
    "BLOCK_KINDS",
    "DEFAULT_LOOP_COUNT",
    "KIND_LABELS",
    "KIND_ORDER",
    "KINDS",
    "MARKER_KINDS",
    "Operator",
    "OperatorExtra",
    "SCHEMA_VERSION",
    "SLOT_COUNT",
    "Step",
    "Team",
    "TimelineModel",
    "TimelineService",
    "create_service",
    "normalize_loop_count",
    "share",
]
