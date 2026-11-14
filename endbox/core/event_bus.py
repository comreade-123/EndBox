# -*- coding: utf-8 -*-
"""进程内事件总线。

模块之间只通过事件通信，插件默认只能订阅白名单里的事件。
事件名为点分字符串，例如 ``timeline.tick``、``account.updated``。
"""
from __future__ import annotations

import threading
from typing import Any, Callable

from endbox.core.log import get_logger

log = get_logger("core.events")

Handler = Callable[..., None]


class Subscription:
    def __init__(self, bus: "EventBus", topic: str, handler: Handler):
        self.bus = bus
        self.topic = topic
        self.handler = handler
        self.cancelled = False

    def cancel(self) -> None:
        if not self.cancelled:
            self.bus.unsubscribe(self)
            self.cancelled = True


class EventBus:
    """线程安全；处理函数异常被捕获，不会推翻发布方。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._exact: dict[str, list[Subscription]] = {}
        self._prefix: dict[str, list[Subscription]] = {}
        self._history: list[tuple[str, dict[str, Any]]] = []

    def subscribe(self, topic: str, handler: Handler) -> Subscription:
        sub = Subscription(self, topic, handler)
        with self._lock:
            self._exact.setdefault(topic, []).append(sub)
        return sub

    def subscribe_prefix(self, prefix: str, handler: Handler) -> Subscription:
        sub = Subscription(self, prefix, handler)
        with self._lock:
            self._prefix.setdefault(prefix, []).append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            for bucket in (self._exact, self._prefix):
                for topic, subs in list(bucket.items()):
                    if sub in subs:
                        subs.remove(sub)
                        if not subs:
                            bucket.pop(topic, None)

    def emit(self, topic: str, **payload: Any) -> None:
        with self._lock:
            targets = list(self._exact.get(topic, ()))
            for prefix, subs in self._prefix.items():
                if topic.startswith(prefix):
                    targets.extend(subs)
            self._history.append((topic, payload))
            if len(self._history) > 256:
                self._history = self._history[-128:]
        for sub in targets:
            if sub.cancelled:
                continue
            try:
                sub.handler(**payload)
            except Exception:  # noqa: BLE001
                log.exception("event handler failed for %s", topic)

    def history(self, topic: str | None = None) -> list[tuple[str, dict[str, Any]]]:
        with self._lock:
            if topic is None:
                return list(self._history)
            return [(t, p) for t, p in self._history if t == topic]


bus = EventBus()


# 全局事件主题（写在这里，模块与插件都引用同一份）
class Topics:
    VIEW_CHANGED = "ui.view_changed"
    THEME_CHANGED = "ui.theme_changed"
    TOAST = "ui.toast"
    ACCOUNT_UPDATED = "account.updated"
    ACCOUNT_STATE = "account.state"
    TIMELINE_STATE = "timeline.state"
    TIMELINE_TICK = "timeline.tick"
    TIMELINE_CHANGED = "timeline.changed"
    HUD_TOGGLE = "hud.toggle"
    HUD_STATE = "hud.state"
    PLUGINS_CHANGED = "plugins.changed"
