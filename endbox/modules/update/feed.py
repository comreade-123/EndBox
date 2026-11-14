# -*- coding: utf-8 -*-
"""更新来源解析：GitHub Releases / 仓库内 version.json / CHANGELOG。

这一层是纯数据：不发网络请求、不依赖 Qt、不读写设置，方便单测。

两个来源的定位：
  · GitHub Releases —— 发布后自动生效，带 release notes 与下载附件；
  · 仓库内 version.json —— 轻量、可直连 raw，GitHub API 不可达或限流时的兜底。

两者解析成同一个 :class:`ReleaseInfo`，后续版本比较 / UI 渲染只认这一种结构。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from endbox.core.text import t
from endbox.version import compare_versions, is_valid_version, normalize_version

SOURCE_GITHUB = "github"
SOURCE_VERSION_JSON = "version_json"
SOURCE_CHANGELOG = "changelog"

#: GitHub Releases API 默认地址（发布后自动可用）
GITHUB_RELEASES_API = "https://api.github.com/repos/comreade-123/EndBox/releases"
#: 仓库内 version.json 的 raw 地址（官方源）
VERSION_JSON_URL = "https://raw.githubusercontent.com/comreade-123/EndBox/main/version.json"
#: 发布页
RELEASES_PAGE = "https://github.com/comreade-123/EndBox/releases"

_ASSET_NAMES = (
    (".exe", "exe"),
    (".zip", "zip"),
    (".7z", "zip"),
    (".tar.gz", "zip"),
)


def classify_asset(name: str) -> str:
    """按附件名判断下载类型：``exe`` / ``zip`` / ``other``。"""
    lowered = str(name or "").strip().lower()
    for suffix, kind in _ASSET_NAMES:
        if lowered.endswith(suffix):
            return kind
    return "other"


@dataclass
class ReleaseAsset:
    """一个下载附件。"""

    name: str
    url: str
    size: int = 0
    kind: str = "other"

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "url": self.url, "size": self.size, "kind": self.kind}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ReleaseAsset":
        name = str(raw.get("name") or "")
        return cls(
            name=name,
            url=str(raw.get("url") or raw.get("browser_download_url") or ""),
            size=_as_int(raw.get("size"), 0),
            kind=str(raw.get("kind") or classify_asset(name)),
        )


@dataclass
class ReleaseInfo:
    """一个版本的发布信息（两个来源统一成这个结构）。"""

    version: str
    title: str = ""
    notes: str = ""
    published_at: str = ""
    html_url: str = ""
    download_url: str = ""
    download_kind: str = ""
    assets: list[ReleaseAsset] = field(default_factory=list)
    source: str = SOURCE_GITHUB
    prerelease: bool = False
    #: 兼容旧版本的下限（version.json 可选），不满足时提示用户
    min_version: str = ""

    # -- 查询 ---------------------------------------------------------------
    def is_newer_than(self, current: str) -> bool:
        return compare_versions(self.version, current) > 0

    def best_download(self) -> tuple[str, str]:
        """返回 ``(url, kind)``：优先 exe，其次 zip，最后发布页。"""
        if self.download_url:
            return self.download_url, self.download_kind or classify_asset(self.download_url)
        for kind in ("exe", "zip"):
            for asset in self.assets:
                if asset.kind == kind and asset.url:
                    return asset.url, kind
        return self.html_url, "page"

    def display_notes(self) -> str:
        text = str(self.notes or "").strip()
        return text or t("update.notes_empty")

    # -- 序列化（缓存用） ---------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "title": self.title,
            "notes": self.notes,
            "published_at": self.published_at,
            "html_url": self.html_url,
            "download_url": self.download_url,
            "download_kind": self.download_kind,
            "assets": [asset.to_dict() for asset in self.assets],
            "source": self.source,
            "prerelease": self.prerelease,
            "min_version": self.min_version,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ReleaseInfo":
        assets = [
            ReleaseAsset.from_dict(item)
            for item in (raw.get("assets") or [])
            if isinstance(item, dict)
        ]
        return cls(
            version=normalize_version(str(raw.get("version") or "")),
            title=str(raw.get("title") or ""),
            notes=str(raw.get("notes") or ""),
            published_at=str(raw.get("published_at") or ""),
            html_url=str(raw.get("html_url") or ""),
            download_url=str(raw.get("download_url") or ""),
            download_kind=str(raw.get("download_kind") or ""),
            assets=assets,
            source=str(raw.get("source") or SOURCE_GITHUB),
            prerelease=bool(raw.get("prerelease")),
            min_version=str(raw.get("min_version") or ""),
        )


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _notes_text(value: Any) -> str:
    """release body / version.json notes 允许是字符串或字符串数组。"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        lines = [str(item).strip() for item in value if str(item).strip()]
        return "\n".join(lines)
    return ""


def parse_github_release(payload: dict[str, Any]) -> ReleaseInfo | None:
    """解析单个 GitHub Release 对象；草稿返回 None，没有 tag 返回 None。"""
    if not isinstance(payload, dict):
        return None
    if payload.get("draft"):
        return None
    tag = str(payload.get("tag_name") or payload.get("name") or "").strip()
    if not is_valid_version(tag):
        return None
    assets: list[ReleaseAsset] = []
    for item in payload.get("assets") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        url = str(item.get("browser_download_url") or item.get("url") or "")
        if not url:
            continue
        assets.append(
            ReleaseAsset(
                name=name,
                url=url,
                size=_as_int(item.get("size"), 0),
                kind=classify_asset(name),
            )
        )
    html_url = str(payload.get("html_url") or "")
    if not html_url:
        html_url = f"{RELEASES_PAGE}/tag/v{normalize_version(tag)}"
    return ReleaseInfo(
        version=normalize_version(tag),
        title=str(payload.get("name") or "").strip(),
        notes=_notes_text(payload.get("body")),
        published_at=str(payload.get("published_at") or payload.get("created_at") or ""),
        html_url=html_url,
        assets=assets,
        source=SOURCE_GITHUB,
        prerelease=bool(payload.get("prerelease")),
    )


def parse_github_releases(payload: Any) -> list[ReleaseInfo]:
    """解析 Releases 列表，按版本从新到旧排序（草稿被丢弃）。"""
    if isinstance(payload, dict):
        payload = payload.get("releases") if isinstance(payload.get("releases"), list) else [payload]
    if not isinstance(payload, list):
        raise ValueError(t("update.error.bad_payload"))
    releases = [
        release
        for release in (parse_github_release(item) for item in payload if isinstance(item, dict))
        if release is not None
    ]
    releases.sort(key=lambda item: _release_key(item), reverse=True)
    return releases


def _release_key(release: ReleaseInfo) -> tuple:
    from endbox.version import version_key

    return version_key(release.version)


def pick_latest_release(releases: list[ReleaseInfo]) -> ReleaseInfo | None:
    """选最新正式版；没有正式版时退回最新预发布版。"""
    stable = [release for release in releases if not release.prerelease]
    pool = stable or releases
    if not pool:
        return None
    return max(pool, key=_release_key)


def parse_version_payload(payload: Any) -> ReleaseInfo:
    """解析仓库内 ``version.json``。

    兼容三种下载声明：``downloads`` 字典、``download_url`` 字符串、
    ``assets`` 数组；缺下载地址时退回 ``html_url``（发布页）。
    """
    if not isinstance(payload, dict):
        raise ValueError(t("update.error.bad_payload"))
    version = str(payload.get("version") or payload.get("latest") or "").strip()
    if not is_valid_version(version):
        raise ValueError(t("update.error.bad_payload"))
    version = normalize_version(version)

    assets: list[ReleaseAsset] = []
    downloads = payload.get("downloads")
    if isinstance(downloads, dict):
        for kind, url in downloads.items():
            url_text = str(url or "").strip()
            if not url_text:
                continue
            kind_text = str(kind or "").strip().lower()
            if kind_text not in ("exe", "zip"):
                kind_text = classify_asset(url_text)
            assets.append(
                ReleaseAsset(name=url_text.rsplit("/", 1)[-1], url=url_text, kind=kind_text)
            )
    for item in payload.get("assets") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("download_url") or "")
        if not url:
            continue
        name = str(item.get("name") or url.rsplit("/", 1)[-1])
        assets.append(
            ReleaseAsset(
                name=name,
                url=url,
                size=_as_int(item.get("size"), 0),
                kind=str(item.get("kind") or classify_asset(name)),
            )
        )
    download_url = str(payload.get("download_url") or "").strip()
    download_kind = classify_asset(download_url) if download_url else ""

    html_url = str(payload.get("html_url") or "").strip()
    if not html_url:
        html_url = f"{RELEASES_PAGE}/tag/v{version}"
    return ReleaseInfo(
        version=version,
        title=str(payload.get("title") or "").strip(),
        notes=_notes_text(payload.get("notes") or payload.get("body")),
        published_at=str(payload.get("released") or payload.get("published_at") or ""),
        html_url=html_url,
        download_url=download_url,
        download_kind=download_kind,
        assets=assets,
        source=SOURCE_VERSION_JSON,
        min_version=normalize_version(str(payload.get("min_version") or ""))
        if is_valid_version(str(payload.get("min_version") or ""))
        else "",
    )


# ---------------------------------------------------------------------------
# CHANGELOG.md 解析（离线也能展示更新日志）
# ---------------------------------------------------------------------------
_HEADING_RE = re.compile(
    r"^##\s+\[?[vV]?(\d{1,5}(?:\.\d{1,5}){0,2}(?:-[0-9A-Za-z.]+)?)\]?"
    r"(?:\s*[-–—]\s*([0-9]{4}-[0-9]{2}-[0-9]{2}))?\s*$"
)


def parse_changelog(text: str) -> list[dict[str, str]]:
    """把 CHANGELOG.md 解析成 ``[{version, date, body}]``，按文件顺序（新→旧）。"""
    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    body: list[str] = []

    def flush() -> None:
        if current is None:
            return
        current["body"] = "\n".join(body).strip()
        entries.append(current)

    for line in str(text or "").splitlines():
        match = _HEADING_RE.match(line.strip())
        if match is not None:
            flush()
            current = {
                "version": normalize_version(match.group(1)),
                "date": match.group(2) or "",
            }
            body = []
            continue
        if current is not None:
            body.append(line)
    flush()
    return entries


def changelog_notes(text: str, version: str) -> str:
    """从 CHANGELOG 文本里取某个版本的正文；没有返回空串。"""
    for entry in parse_changelog(text):
        if compare_versions(entry["version"], version) == 0:
            return entry["body"]
    return ""


__all__ = [
    "RELEASES_PAGE",
    "GITHUB_RELEASES_API",
    "SOURCE_CHANGELOG",
    "SOURCE_GITHUB",
    "SOURCE_VERSION_JSON",
    "VERSION_JSON_URL",
    "ReleaseAsset",
    "ReleaseInfo",
    "changelog_notes",
    "classify_asset",
    "parse_changelog",
    "parse_github_release",
    "parse_github_releases",
    "parse_version_payload",
    "pick_latest_release",
]
