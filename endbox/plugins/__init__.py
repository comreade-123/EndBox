# -*- coding: utf-8 -*-
"""EndBox 插件系统。

宿主入口：``from endbox.plugins.host import PluginHost``。
插件作者只需要 ``from endbox.plugins.api import EndBoxPlugin``。
"""
from __future__ import annotations

from endbox.plugins.api import (
    ALLOWED_EVENT_PREFIXES,
    ALLOWED_EVENT_TOPICS,
    EndBoxPlugin,
    PluginContext,
    PluginEventBus,
    PluginStorage,
    topic_allowed,
)
from endbox.plugins.host import (
    STATE_DISABLED,
    STATE_ERROR,
    STATE_LOADED,
    PluginHost,
    PluginRecord,
)
from endbox.plugins.manifest import ALLOWED_PERMISSIONS, ManifestError, PluginManifest

__all__ = [
    "ALLOWED_EVENT_PREFIXES",
    "ALLOWED_EVENT_TOPICS",
    "ALLOWED_PERMISSIONS",
    "EndBoxPlugin",
    "ManifestError",
    "PluginContext",
    "PluginEventBus",
    "PluginHost",
    "PluginManifest",
    "PluginRecord",
    "PluginStorage",
    "STATE_DISABLED",
    "STATE_ERROR",
    "STATE_LOADED",
    "topic_allowed",
]
