# -*- coding: utf-8 -*-
"""插件开发者 API：``EndBoxPlugin`` 基类与受限的 ``PluginContext``。

插件拿不到 AppContext / 服务对象 / 主窗口，只能通过这里的白名单接口工作：

  · ``register_view()``      注册视图（宿主自动加 ``plugin_<id>_`` 前缀、section="plugin"）
  · ``timeline_snapshot()``  需要 ``timeline:read``
  · ``account_snapshot()``   需要 ``account:read``
  · ``event_bus.subscribe()`` 只允许 ``timeline.*`` / ``account.updated`` / ``ui.theme_changed``
  · ``storage``              插件私有 JSON 持久化

越权访问抛 ``PermissionError`` 并写入插件日志；宿主按「插件加载失败」隔离插件。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from endbox.core.event_bus import Subscription
from endbox.core.log import get_logger
from endbox.core.text import t
from endbox.ui.icons import has_icon
from endbox.ui.views.base import BaseView

from endbox.plugins.manifest import ALLOWED_PERMISSIONS, PluginManifest
from endbox.plugins.storage import PluginStorage

#: 插件被允许订阅的事件：精确匹配 + 前缀匹配。
ALLOWED_EVENT_TOPICS = ("account.updated", "ui.theme_changed")
ALLOWED_EVENT_PREFIXES = ("timeline.",)


def topic_allowed(topic: str) -> bool:
    topic = str(topic or "")
    return topic in ALLOWED_EVENT_TOPICS or topic.startswith(ALLOWED_EVENT_PREFIXES)


def _slugify(text: str, *, fallback: str = "view") -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", str(text or "").lower()).strip("_")
    return slug or fallback


class EndBoxPlugin:
    """所有插件入口类的基类。生命周期方法都可选、都允许留空。"""

    ctx: "PluginContext"

    def __init__(self, ctx: "PluginContext | None" = None):
        self.ctx = ctx  # type: ignore[assignment]

    def on_load(self) -> None:
        """插件被导入后调用：注册视图、准备数据。"""

    def on_enable(self) -> None:
        """插件启用后调用（首次加载时在 on_load 之后）。"""

    def on_disable(self) -> None:
        """插件被禁用前调用：停掉定时器、释放资源。"""

    def on_unload(self) -> None:
        """插件被卸载 / 重新加载 / 程序退出前调用。"""


class PluginEventBus:
    """插件侧事件总线门面：只允许白名单主题。"""

    def __init__(self, owner: "PluginContext"):
        self._owner = owner
        self._subscriptions: list[Subscription] = []
        self._closed = False

    def subscribe(self, topic: str, handler: Callable[..., None]) -> Subscription:
        topic = str(topic or "")
        if not topic_allowed(topic):
            message = t("plugins.api.error.topic_denied", id=self._owner.plugin_id, topic=topic)
            self._owner.logger.warning("event subscription denied: %s", topic)
            raise PermissionError(message)
        if not callable(handler):
            raise TypeError(t("plugins.api.error.handler_not_callable"))
        if self._closed:
            raise RuntimeError(t("plugins.api.error.closed_subscribe"))

        def wrapped(**payload: Any) -> None:
            try:
                handler(**payload)
            except BaseException:  # noqa: BLE001 - 插件处理函数崩溃不能影响发布方
                self._owner.logger.exception("事件处理失败：%s", topic)

        subscription = self._owner._app.bus.subscribe(topic, wrapped)
        self._subscriptions.append(subscription)
        return subscription

    @property
    def allowed_topics(self) -> tuple[str, ...]:
        return ALLOWED_EVENT_TOPICS + ALLOWED_EVENT_PREFIXES

    def cancel_all(self) -> None:
        self._closed = True
        for subscription in self._subscriptions:
            try:
                subscription.cancel()
            except Exception:  # noqa: BLE001
                pass
        self._subscriptions.clear()


class PluginContext:
    """插件能看到的全部宿主能力。由 PluginHost 为每个插件单独构造。"""

    def __init__(
        self,
        *,
        host: Any,
        app_ctx: Any,
        record: Any,
        manifest: PluginManifest,
        plugin_dir: Path,
        storage: PluginStorage,
    ):
        self._host = host
        self._app = app_ctx
        self._record = record
        self.manifest = manifest
        self.plugin_id = manifest.id
        self.plugin_dir = Path(plugin_dir)
        self.storage = storage
        self.logger = get_logger(f"plugins.{manifest.id}")
        self._event_bus = PluginEventBus(self)
        self._closed = False

    # -- 视图 ---------------------------------------------------------------
    def register_view(
        self,
        view_cls: type[BaseView],
        *,
        sidebar_icon: str | None = None,
        sidebar_label: str | None = None,
        order: int | None = None,
        title: str | None = None,
        eyebrow: str | None = None,
    ) -> type[BaseView]:
        """注册一个插件视图。

        宿主不修改传入的视图类，而是动态生成一个子类：
        ``key = plugin_<插件id>_<原key>``、``section = "plugin"``，
        侧边栏会自动把它放进「插件」分区。
        """
        if not (isinstance(view_cls, type) and issubclass(view_cls, BaseView)):
            raise TypeError(t("plugins.api.error.register_type"))
        if self._closed:
            raise RuntimeError(t("plugins.api.error.closed_register"))

        base_key = str(getattr(view_cls, "key", "") or view_cls.__name__)
        slug = _slugify(base_key)
        title_text = (title or getattr(view_cls, "title", "") or sidebar_label or slug).strip()
        eyebrow_text = (eyebrow or getattr(view_cls, "eyebrow", "") or title_text).strip()
        label = (sidebar_label or getattr(view_cls, "sidebar_label", "") or title_text).strip()[:6]
        icon = (sidebar_icon or getattr(view_cls, "icon", "") or "plugin").strip()
        if not has_icon(icon):
            icon = "plugin"

        key = f"plugin_{_slugify(self.plugin_id, fallback='plugin')}_{slug}"
        attrs: dict[str, Any] = {
            "key": key,
            "section": "plugin",
            "title": title_text,
            "eyebrow": eyebrow_text,
            "icon": icon,
            "sidebar_label": label,
            "description": getattr(view_cls, "description", ""),
            "__module__": getattr(view_cls, "__module__", __name__),
            "__doc__": t("plugins.api.view_doc", id=self.plugin_id),
        }
        if order is not None:
            try:
                attrs["order"] = int(order)
            except (TypeError, ValueError):
                pass
        generated = type(f"{view_cls.__name__}_{_slugify(self.plugin_id)}", (view_cls,), attrs)
        self._host.register_plugin_view(self._record, generated)
        return generated

    # -- 只读快照（需要权限） ------------------------------------------------
    def timeline_snapshot(self) -> dict[str, Any]:
        """排轴快照（只读）。需要 ``timeline:read``；服务未载入时返回空字典。"""
        self._require_permission("timeline:read", "timeline_snapshot")
        service = getattr(self._app.services, "timeline", None)
        if service is None:
            self.logger.info("timeline_snapshot(): 排轴服务未载入，返回空快照")
            return {}
        return self._call_snapshot(service, "timeline_snapshot")

    def account_snapshot(self) -> dict[str, Any]:
        """账号快照（只读）。需要 ``account:read``；服务未载入时返回空字典。"""
        self._require_permission("account:read", "account_snapshot")
        service = getattr(self._app.services, "account", None)
        if service is None:
            self.logger.info("account_snapshot(): 账号服务未载入，返回空快照")
            return {}
        return self._call_snapshot(service, "account_snapshot")

    def _call_snapshot(self, service: Any, api: str) -> dict[str, Any]:
        snapshot = getattr(service, "snapshot", None)
        if not callable(snapshot):
            self.logger.warning("%s(): 服务没有 snapshot()，返回空快照", api)
            return {}
        try:
            data = snapshot()
        except Exception:  # noqa: BLE001 - 服务内部错误不拖垮插件
            self.logger.exception("%s(): 服务快照读取失败，返回空快照", api)
            return {}
        return dict(data) if isinstance(data, dict) else {}

    def _require_permission(self, permission: str, api: str) -> None:
        if permission in self.manifest.permissions:
            return
        message = t("plugins.api.error.permission_denied", id=self.plugin_id, permission=permission, api=api)
        self.logger.warning("permission denied: %s -> %s()", permission, api)
        raise PermissionError(message)

    # -- 事件 ---------------------------------------------------------------
    @property
    def event_bus(self) -> PluginEventBus:
        return self._event_bus

    # -- 便捷 ---------------------------------------------------------------
    def notify(self, text: str, kind: str = "info") -> None:
        """弹一条全局 Toast（插件生命周期里也可调用）。"""
        try:
            self._app.notify(text, kind)
        except Exception:  # noqa: BLE001
            self.logger.exception("notify failed")

    # -- 宿主内部 -----------------------------------------------------------
    def close(self) -> None:
        self._closed = True
        self._event_bus.cancel_all()


__all__ = [
    "ALLOWED_EVENT_PREFIXES",
    "ALLOWED_EVENT_TOPICS",
    "ALLOWED_PERMISSIONS",
    "EndBoxPlugin",
    "PluginContext",
    "PluginEventBus",
    "PluginStorage",
    "topic_allowed",
]
