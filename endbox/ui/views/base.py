# -*- coding: utf-8 -*-
"""视图基类与注册表。

每个功能模块 = 一个 BaseView 子类。侧边栏、顶栏标题都由类属性驱动。
插件通过 PluginContext.register_view 注册的视图走同一套机制，只是 section="plugin"。
"""
from __future__ import annotations

from typing import Iterable

from PyQt6.QtWidgets import QVBoxLayout, QWidget

from endbox.app_context import AppContext


class BaseView(QWidget):
    key: str = ""
    eyebrow: str = ""
    title: str = ""
    icon: str = "grid"
    order: int = 100
    section: str = "core"          # core | plugin
    sidebar_label: str = ""
    description: str = ""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName(f"view_{self.key}")
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)
        self.build()

    # -- 子类实现 -----------------------------------------------------------
    def build(self) -> None:  # pragma: no cover - 由子类实现
        raise NotImplementedError

    def on_show(self) -> None:
        """视图被切到前台。"""

    def on_hide(self) -> None:
        """视图离开前台；轮询、动画等应在此停止。"""

    def refresh(self) -> None:
        """外部数据变化后由宿主调用。"""

    def topbar_fields(self) -> list[str]:
        """顶栏右侧的字段（编号、日期、计数）。"""
        return []

    def topbar_actions(self) -> list[QWidget]:
        """顶栏右侧的操作按钮。"""
        return []

    # -- 便捷 ---------------------------------------------------------------
    @property
    def nav_label(self) -> str:
        return self.sidebar_label or self.title or self.key

    def add(self, widget: QWidget) -> QWidget:
        self._root.addWidget(widget)
        return widget


class ViewRegistry:
    """按 order 排序的视图注册表。"""

    def __init__(self) -> None:
        self._views: dict[str, type[BaseView]] = {}
        self._instances: dict[str, BaseView] = {}
        self._listeners: list = []

    def register(self, view_cls: type[BaseView]) -> None:
        key = getattr(view_cls, "key", "")
        if not key:
            raise ValueError(f"view {view_cls!r} has no key")
        self._views[key] = view_cls
        self._notify()

    def unregister(self, key: str) -> None:
        self._views.pop(key, None)
        instance = self._instances.pop(key, None)
        if instance is not None:
            instance.deleteLater()
        self._notify()

    def on_changed(self, callback) -> None:
        self._listeners.append(callback)

    def _notify(self) -> None:
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:  # noqa: BLE001
                pass

    def classes(self, section: str | None = None) -> list[type[BaseView]]:
        items = sorted(self._views.values(), key=lambda c: (c.section == "plugin", c.order, c.title))
        if section is not None:
            items = [c for c in items if c.section == section]
        return items

    def has(self, key: str) -> bool:
        return key in self._views

    def get(self, key: str) -> type[BaseView] | None:
        return self._views.get(key)

    def instance(self, key: str, ctx: AppContext) -> BaseView | None:
        if key not in self._views:
            return None
        if key not in self._instances:
            self._instances[key] = self._views[key](ctx)
        return self._instances[key]

    def instances(self) -> Iterable[BaseView]:
        return list(self._instances.values())
