# -*- coding: utf-8 -*-
"""EndBox 版本信息与语义化版本比较：全项目唯一的版本来源。

这里的 ``__version__`` 是**程序自身**的版本，也是打包、更新检查、插件
``min_app_version`` 兼容性判断的唯一来源。

仓库根目录的 ``version.json`` 是给「检查更新」用的**远程发布声明**
（最新版本 + 下载地址 + 发布说明），它只描述「线上最新版是什么」，
不反过来决定本地版本 —— 否则源码已经升级、version.json 还没改成新版
的时候，程序会把自己当成旧版本，更新检查逻辑就乱了。

发布流程见 ``docs/update.md``：改 ``endbox/version.py`` 与 ``version.json``、
打 tag、发 release。
"""
from __future__ import annotations

import os
import re

#: 应用版本（语义化版本，发布时改这里）
__version__ = "1.1.0"
VERSION = __version__

APP_NAME = "EndBox"
__author__ = "EthanMaven、DeepSeek-V4.1-Flash"
APP_CODE = "ENDBOX"

#: GitHub 仓库（更新检查与插件市场默认下载地址都基于它）
GITHUB_OWNER = "comreade-123"
GITHUB_REPO = "EndBox"
GITHUB_REPO_SLUG = f"{GITHUB_OWNER}/{GITHUB_REPO}"
GITHUB_RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO_SLUG}/releases"
GITHUB_LATEST_RELEASE_API = f"{GITHUB_RELEASES_API}/latest"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO_SLUG}/releases"
GITHUB_TAG_URL = f"https://github.com/{GITHUB_REPO_SLUG}/releases/tag/v{{version}}"
VERSION_JSON_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO_SLUG}/main/version.json"
CHANGELOG_URL = f"https://github.com/{GITHUB_REPO_SLUG}/blob/main/CHANGELOG.md"

#: 允许 ``v1.2.3``、``1.2``、``1``、``1.2.3-beta.1`` 这类写法；捕获预发布标签
_VERSION_RE = re.compile(
    r"^[vV]?\s*(\d{1,5})(?:\.(\d{1,5}))?(?:\.(\d{1,5}))?"
    r"(?:[-+]?([0-9A-Za-z][0-9A-Za-z.+-]{0,63}))?$"
)


def normalize_version(value: str) -> str:
    """把 ``v1.2`` 规范成 ``1.2.0``；无法识别时原样返回（去空白）。"""
    text = str(value or "").strip()
    try:
        major, minor, patch, pre = _parse(value)
    except ValueError:
        return text
    base = f"{major}.{minor}.{patch}"
    return f"{base}-{pre}" if pre else base


def _parse(value: str) -> tuple[int, int, int, str]:
    match = _VERSION_RE.match(str(value or "").strip())
    if match is None:
        raise ValueError(f"不是合法的版本号：{value!r}")
    major = int(match.group(1))
    minor = int(match.group(2) or 0)
    patch = int(match.group(3) or 0)
    pre = str(match.group(4) or "")
    return major, minor, patch, pre


def is_valid_version(value: str) -> bool:
    try:
        _parse(value)
    except ValueError:
        return False
    return True


def version_tuple(value: str) -> tuple[int, int, int]:
    """数字部分，例如 ``v1.2.3-beta`` -> ``(1, 2, 3)``。非法输入返回 ``(0, 0, 0)``。"""
    try:
        major, minor, patch, _pre = _parse(value)
    except ValueError:
        return (0, 0, 0)
    return (major, minor, patch)


def version_key(value: str) -> tuple[int, int, int, int, str]:
    """可排序的版本键：正式版 > 同号预发布版。"""
    try:
        major, minor, patch, pre = _parse(value)
    except ValueError:
        return (0, 0, 0, 0, "")
    return (major, minor, patch, 0 if pre else 1, pre)


def compare_versions(left: str, right: str) -> int:
    """比较两个版本：``left`` 新返回 1，相同返回 0，更旧返回 -1。

    预发布语义：``1.1.0-beta`` < ``1.1.0``。非法版本号按 ``0.0.0`` 处理。
    """
    left_key = version_key(left)
    right_key = version_key(right)
    if left_key == right_key:
        return 0
    return 1 if left_key > right_key else -1


def is_newer(candidate: str, current: str) -> bool:
    """``candidate`` 是否比 ``current`` 新。"""
    return compare_versions(candidate, current) > 0


def current_version() -> str:
    """当前运行版本。

    ``ENDBOX_VERSION`` 可用于开发/打包测试时覆盖显示版本（合法才生效）。
    """
    override = os.environ.get("ENDBOX_VERSION", "").strip()
    if override and is_valid_version(override):
        return normalize_version(override)
    return __version__


def version_label() -> str:
    """界面用短标签：``v1.1.0``。"""
    return f"v{current_version()}"


__all__ = [
    "APP_CODE",
    "APP_NAME",
    "CHANGELOG_URL",
    "GITHUB_LATEST_RELEASE_API",
    "GITHUB_OWNER",
    "GITHUB_RELEASES_API",
    "GITHUB_RELEASES_URL",
    "GITHUB_REPO",
    "GITHUB_REPO_SLUG",
    "GITHUB_TAG_URL",
    "VERSION",
    "VERSION_JSON_URL",
    "__author__",
    "__version__",
    "compare_versions",
    "current_version",
    "is_newer",
    "is_valid_version",
    "normalize_version",
    "version_key",
    "version_label",
    "version_tuple",
]
