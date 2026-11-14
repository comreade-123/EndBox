# -*- coding: utf-8 -*-
"""应用上下文：视图能拿到的一切共享对象。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Any

from endbox.config import Settings
from endbox.core.event_bus import EventBus
from endbox.services import Services


@dataclass
class AppContext:
    settings: Settings
    bus: EventBus
    services: Services
    open_view: Callable[[str], None] = field(default=lambda _key: None)
    toast: Callable[..., None] = field(default=lambda *_a, **_k: None)
    main_window: Any = None

    def notify(self, text: str, kind: str = "info") -> None:
        try:
            self.toast(text, kind)
        except Exception:  # noqa: BLE001
            pass
