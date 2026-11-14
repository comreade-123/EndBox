# -*- coding: utf-8 -*-
"""plugin.json 的解析与校验。

坏 manifest 只产生一个 ``ManifestError``，由宿主记成「加载失败」记录，
绝不向上冒泡影响主程序。校验规则尽量给出人话中文错误。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from endbox.core.log import get_logger
from endbox.core.text import t
from endbox.version import __version__

log = get_logger("plugins.manifest")

#: 宿主当前支持的权限白名单（docs/CONTRACT.md §10）
ALLOWED_PERMISSIONS: tuple[str, ...] = ("timeline:read", "account:read")

_PLUGIN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class ManifestError(ValueError):
    """plugin.json 不合规；消息直接展示在插件管理页。"""


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", str(value or ""))
    return tuple(int(p) for p in parts[:4]) or (0,)


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class PluginManifest:
    """校验后的插件清单。只有 ``permissions`` 里是白名单内的权限。"""

    id: str
    name: str
    version: str = "0.0.0"
    author: str = ""
    description: str = ""
    icon: str = "plugin"
    entry: str = "plugin.py"
    order: int = 100
    min_app_version: str = ""
    permissions: list[str] = field(default_factory=list)
    ignored_permissions: list[str] = field(default_factory=list)

    # -- 解析 ---------------------------------------------------------------
    @classmethod
    def load(cls, plugin_dir: Path) -> "PluginManifest":
        plugin_dir = Path(plugin_dir)
        path = plugin_dir / "plugin.json"
        if not path.is_file():
            raise ManifestError(t("plugins.manifest.error.missing_file"))
        try:
            raw = json.loads(path.read_text("utf-8"))
        except Exception as exc:  # noqa: BLE001 - JSON 损坏是常见情况
            raise ManifestError(t("plugins.manifest.error.bad_json", error=exc)) from exc
        if not isinstance(raw, dict):
            raise ManifestError(t("plugins.manifest.error.not_object"))
        return cls.from_dict(raw, plugin_dir)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], plugin_dir: Path) -> "PluginManifest":
        plugin_dir = Path(plugin_dir)

        plugin_id = str(raw.get("id") or "").strip()
        if not plugin_id:
            raise ManifestError(t("plugins.manifest.error.missing_id"))
        if not _PLUGIN_ID.match(plugin_id):
            raise ManifestError(t("plugins.manifest.error.bad_id", id=plugin_id))

        name = str(raw.get("name") or "").strip() or plugin_id
        version = str(raw.get("version") or "0.0.0").strip() or "0.0.0"

        required = str(raw.get("min_app_version") or "").strip()
        if required and _version_tuple(required) > _version_tuple(__version__):
            raise ManifestError(
                t("plugins.manifest.error.min_version", required=required, current=__version__)
            )

        entry = str(raw.get("entry") or "plugin.py").strip()
        entry_path = Path(entry)
        if entry_path.is_absolute() or ".." in entry_path.parts:
            raise ManifestError(t("plugins.manifest.error.entry_abs", entry=entry))
        if not (plugin_dir / entry_path).is_file():
            raise ManifestError(t("plugins.manifest.error.entry_missing", entry=entry))

        permissions: list[str] = []
        ignored: list[str] = []
        declared = raw.get("permissions", [])
        if declared is None:
            declared = []
        if not isinstance(declared, list):
            raise ManifestError(t("plugins.manifest.error.permissions"))
        for item in declared:
            if not isinstance(item, str):
                raise ManifestError(t("plugins.manifest.error.permissions"))
            permission = item.strip()
            if not permission:
                continue
            if permission in ALLOWED_PERMISSIONS:
                if permission not in permissions:
                    permissions.append(permission)
            elif permission not in ignored:
                ignored.append(permission)
        if ignored:
            log.warning("plugin %s: 忽略未知权限 %s", plugin_id, ", ".join(ignored))

        icon = str(raw.get("icon") or "plugin").strip() or "plugin"
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", icon):
            icon = "plugin"

        return cls(
            id=plugin_id,
            name=name,
            version=version,
            author=str(raw.get("author") or "").strip(),
            description=str(raw.get("description") or "").strip(),
            icon=icon,
            entry=str(entry_path).replace("\\", "/"),
            order=_as_int(raw.get("order"), 100),
            min_app_version=required,
            permissions=permissions,
            ignored_permissions=ignored,
        )

    # -- 展示 ---------------------------------------------------------------
    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "icon": self.icon,
            "entry": self.entry,
            "order": self.order,
            "min_app_version": self.min_app_version,
            "permissions": list(self.permissions),
            "ignored_permissions": list(self.ignored_permissions),
        }
