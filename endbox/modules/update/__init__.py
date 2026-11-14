# -*- coding: utf-8 -*-
"""版本更新模块。

纯逻辑（``feed`` / ``net`` / ``service``）+ 一个可选的主壳提示部件（``badge``）。

用法::

    from endbox.modules.update import UpdateService

    service = UpdateService(settings=ctx.settings)   # 或 ctx.services.update
    result = service.check()                         # 放 workers.run_async 里调用

发布流程见 ``docs/update.md``。
"""
from __future__ import annotations

from endbox.modules.update.feed import (
    GITHUB_RELEASES_API,
    RELEASES_PAGE,
    SOURCE_CHANGELOG,
    SOURCE_GITHUB,
    SOURCE_VERSION_JSON,
    VERSION_JSON_URL,
    ReleaseAsset,
    ReleaseInfo,
    changelog_notes,
    classify_asset,
    parse_changelog,
    parse_github_release,
    parse_github_releases,
    parse_version_payload,
    pick_latest_release,
)
from endbox.modules.update.service import CheckResult, UpdateService, create_service

__all__ = [
    "GITHUB_RELEASES_API",
    "RELEASES_PAGE",
    "SOURCE_CHANGELOG",
    "SOURCE_GITHUB",
    "SOURCE_VERSION_JSON",
    "VERSION_JSON_URL",
    "CheckResult",
    "ReleaseAsset",
    "ReleaseInfo",
    "UpdateService",
    "changelog_notes",
    "classify_asset",
    "create_service",
    "parse_changelog",
    "parse_github_release",
    "parse_github_releases",
    "parse_version_payload",
    "pick_latest_release",
]
