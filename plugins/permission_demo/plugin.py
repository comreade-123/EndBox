# -*- coding: utf-8 -*-
"""示例插件「权限演示」。

manifest 里 ``permissions`` 是空数组，但视图会真的去调用受保护的
``timeline_snapshot()`` / ``account_snapshot()``：宿主抛 ``PermissionError``、
写入日志，界面把原因和授权方式展示出来。另外演示订阅白名单之外的
``account.state`` 同样会被拒绝。
"""
from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from endbox import config
from endbox.plugins.api import EndBoxPlugin
from endbox.core.text import t
from endbox.ui.kit import (
    CutButton,
    FieldText,
    InfoFlag,
    Label,
    Panel,
    ScrollArea,
)
from endbox.ui.views.base import BaseView


class PermissionDemoView(BaseView):
    key = "permission_demo"
    eyebrow = "PERMISSION DEMO"
    title = t("plugins.permission_demo.title")
    icon = "lock"
    order = 30
    sidebar_label = t("plugins.permission_demo.sidebar_label")
    description = t("plugins.permission_demo.description")

    #: 由插件在 on_load() 里注入
    PLUGIN: "Plugin | None" = None

    def build(self) -> None:
        self._attempted = False

        scroll = ScrollArea(self)
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        manifest_panel = Panel(t("plugins.permission_demo.manifest_panel"), "MANIFEST", padding=16, spacing=10)
        manifest_panel.add(
            Label(
                t("plugins.permission_demo.explain"),
                "muted",
                wrap=True,
            )
        )
        self._perm_field = FieldText(
            [
                t("plugins.permission_demo.permissions_ok"),
                t("plugins.permission_demo.topics_ok"),
            ]
        )
        manifest_panel.add(self._perm_field)
        layout.addWidget(manifest_panel)

        protected = Panel(t("plugins.permission_demo.protected_panel"), "PROTECTED", padding=16, spacing=10)
        buttons = QWidget()
        button_layout = QHBoxLayout(buttons)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(8)
        timeline_btn = CutButton(t("plugins.permission_demo.timeline_btn"), "primary", icon="timeline")
        timeline_btn.clicked.connect(lambda: self._try("timeline_snapshot"))
        button_layout.addWidget(timeline_btn)
        account_btn = CutButton(t("plugins.permission_demo.account_btn"), "ghost", icon="user")
        account_btn.clicked.connect(lambda: self._try("account_snapshot"))
        button_layout.addWidget(account_btn)
        button_layout.addStretch(1)
        protected.add(buttons)

        self._result = InfoFlag(t("plugins.permission_demo.result_initial"), kind="warn")
        protected.add(self._result)
        self._detail = Label("", "mono", wrap=True)
        protected.add(self._detail)
        layout.addWidget(protected)

        allowlist = Panel(t("plugins.permission_demo.allowlist_panel"), "ALLOWLIST", padding=16, spacing=10)
        allowlist.add(
            Label(
                t("plugins.permission_demo.allowlist"),
                "muted",
                wrap=True,
            )
        )
        allowlist.add(
            Label(
                t("plugins.permission_demo.howto"),
                "muted",
                wrap=True,
            )
        )
        allowlist.add(Label(t("plugins.permission_demo.log_file", path=config.LOG_DIR / "endbox.log"), "small", wrap=True))
        layout.addWidget(allowlist)

        layout.addStretch(1)
        scroll.set_content(host)
        self._root.addWidget(scroll)

        # 构建完成后立刻真的调用一次，让首次打开就能看到宿主的拒绝原因
        self._attempted = True
        self._try("timeline_snapshot")

    # -- 生命周期 -----------------------------------------------------------
    def on_show(self) -> None:
        if not self._attempted:
            self._attempted = True
            self._try("timeline_snapshot")

    # -- 越权演示 -----------------------------------------------------------
    def _try(self, api: str) -> None:
        plugin = self.PLUGIN
        if plugin is None:
            self._result.set_text(t("plugins.permission_demo.plugin_missing"))
            self._detail.setText("")
            return
        self._perm_field.set_fields(
            [
                t(
                    "plugins.permission_demo.permission_field",
                    permissions=" / ".join(plugin.ctx.manifest.permissions)
                    or t("plugins.permission_demo.no_permission"),
                ),
                t("plugins.permission_demo.last_try", api=api),
            ]
        )
        try:
            getattr(plugin.ctx, api)()
        except PermissionError as exc:
            self._result.set_text(t("plugins.permission_demo.denied", api=api))
            self._detail.setText(f"{type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            self._result.set_text(
                t("plugins.permission_demo.other_error", api=api, error=type(exc).__name__)
            )
            self._detail.setText(str(exc))
        else:
            self._result.set_text(t("plugins.permission_demo.not_denied", api=api))
            self._detail.setText("")


class Plugin(EndBoxPlugin):
    """只注册视图，不申请任何权限。"""

    def on_load(self) -> None:
        PermissionDemoView.PLUGIN = self
        self.ctx.register_view(
            PermissionDemoView,
            sidebar_icon="lock",
            sidebar_label=t("plugins.permission_demo.sidebar_label"),
            order=30,
        )
        self.ctx.logger.info("permission_demo ready（无任何权限）")

    def on_unload(self) -> None:
        PermissionDemoView.PLUGIN = None
