# -*- coding: utf-8 -*-
"""插件市场：索引解析、缓存、下载校验、安全解压、安装 / 更新 / 卸载。

安全模型（重要）：
  · 市场里的内容**绝不执行**。本模块只做：下载 → 校验 sha256 → 解压落盘 →
    校验 plugin.json；真正加载插件仍由 ``endbox.plugins.host`` 的现有加载器负责。
  · zip slip 防护：拒绝绝对路径、``..``、盘符、符号链接、加密条目；
  · 大小上限：下载包、解压后总字节、文件数量三层限制，防 zip bomb；
  · 只允许「一个顶层插件目录」的包结构，manifest id/版本必须与索引条目一致；
  · 第三方源只允许 http(s)，官方源可用仓库内相对路径（离线也能装）。

所有方法都不依赖 Qt，可在测试里用 ``httpx.MockTransport`` 完全离线跑。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import url2pathname

import httpx

from endbox import config
from endbox.core.log import get_logger
from endbox.core.text import t
from endbox.plugins.manifest import ALLOWED_PERMISSIONS, ManifestError, PluginManifest
from endbox.version import compare_versions, current_version, is_valid_version, normalize_version

log = get_logger("plugins.market")

SCHEMA_VERSION = 1
DEFAULT_CACHE_TTL = 6 * 3600
DEFAULT_TIMEOUT = 15.0
#: 索引文件大小上限（防止恶意大文件）
MAX_INDEX_BYTES = 512 * 1024
#: 单个插件包大小上限
MAX_PACKAGE_BYTES = 32 * 1024 * 1024
#: 解压后总字节上限
MAX_UNCOMPRESSED_BYTES = 96 * 1024 * 1024
#: 解压文件数量上限
MAX_ARCHIVE_FILES = 400
#: 索引里允许的相对路径下载（仅官方源）
_LOCAL_SCHEMES = ("file",)
_REMOTE_SCHEMES = ("http", "https")
_PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_ICON_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

SETTINGS_SOURCES = "plugins.market_sources"


class MarketError(Exception):
    """插件市场操作失败；``str(exc)`` 是可直接展示的中文原因。"""

    def __init__(self, message: str, *, kind: str = "unknown"):
        super().__init__(message)
        self.kind = kind


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class MarketSource:
    """一个市场索引来源。官方源为随程序分发的 ``market/index.json``。"""

    name: str
    url: str = ""
    official: bool = False

    @property
    def key(self) -> str:
        return "official" if self.official else self.url

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "url": self.url}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MarketSource":
        return cls(
            name=str(raw.get("name") or raw.get("url") or "第三方源"),
            url=str(raw.get("url") or ""),
            official=bool(raw.get("official")),
        )


@dataclass
class MarketEntry:
    """市场里的一个插件条目。"""

    id: str
    name: str
    version: str = "0.0.0"
    author: str = ""
    description: str = ""
    icon: str = "plugin"
    category: str = ""
    tags: list[str] = field(default_factory=list)
    homepage: str = ""
    download_url: str = ""
    sha256: str = ""
    size: int = 0
    min_app_version: str = ""
    permissions: list[str] = field(default_factory=list)
    changelog: str = ""
    source_name: str = ""
    official: bool = False

    # -- 展示 ---------------------------------------------------------------
    @property
    def tags_text(self) -> str:
        return " / ".join(self.tags)

    @property
    def download_kind(self) -> str:
        lowered = self.download_url.lower()
        return "zip" if lowered.endswith(".zip") else "file"

    def compatible(self, current: str | None = None) -> bool:
        required = self.min_app_version
        if not required:
            return True
        return compare_versions(required, current or current_version()) <= 0

    def is_update_of(self, installed: str | None) -> bool:
        if not installed:
            return False
        return compare_versions(self.version, installed) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "icon": self.icon,
            "category": self.category,
            "tags": list(self.tags),
            "homepage": self.homepage,
            "download_url": self.download_url,
            "sha256": self.sha256,
            "size": self.size,
            "min_app_version": self.min_app_version,
            "permissions": list(self.permissions),
            "changelog": self.changelog,
        }


@dataclass
class MarketIndex:
    """一个来源解析后的索引。"""

    source: MarketSource
    entries: list[MarketEntry] = field(default_factory=list)
    name: str = ""
    updated: str = ""
    cached: bool = False
    error: str | None = None
    fetched_at: float = 0.0

    def get(self, plugin_id: str) -> MarketEntry | None:
        for entry in self.entries:
            if entry.id == plugin_id:
                return entry
        return None

    def categories(self) -> list[str]:
        found: list[str] = []
        for entry in self.entries:
            category = entry.category.strip()
            if category and category not in found:
                found.append(category)
        return found


@dataclass
class InstalledRecord:
    """安装来源记录（``data/market_state.json``）。"""

    plugin_id: str
    version: str
    source: str = "market"          # market | local
    source_name: str = ""
    installed_at: float = 0.0
    sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "version": self.version,
            "source": self.source,
            "source_name": self.source_name,
            "installed_at": self.installed_at,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "InstalledRecord":
        return cls(
            plugin_id=str(raw.get("plugin_id") or raw.get("id") or ""),
            version=str(raw.get("version") or "0.0.0"),
            source=str(raw.get("source") or "market"),
            source_name=str(raw.get("source_name") or ""),
            installed_at=float(raw.get("installed_at") or 0.0),
            sha256=str(raw.get("sha256") or ""),
        )


@dataclass
class MarketCatalog:
    """多个来源的合并结果。"""

    indexes: list[MarketIndex] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def entries(self) -> list[MarketEntry]:
        entries: list[MarketEntry] = []
        seen: set[str] = set()
        for index in self.indexes:
            for entry in index.entries:
                if entry.id in seen:
                    continue
                seen.add(entry.id)
                entries.append(entry)
        return entries

    def categories(self) -> list[str]:
        found: list[str] = []
        for entry in self.entries:
            category = entry.category.strip()
            if category and category not in found:
                found.append(category)
        return found

    def get(self, plugin_id: str) -> MarketEntry | None:
        for entry in self.entries:
            if entry.id == plugin_id:
                return entry
        return None


@dataclass
class InstallResult:
    ok: bool
    plugin_id: str = ""
    version: str = ""
    path: Path | None = None
    replaced: bool = False
    error: str | None = None


# ---------------------------------------------------------------------------
# 索引解析
# ---------------------------------------------------------------------------


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_url(value: str) -> str:
    return str(value or "").strip()


def _validate_download_url(url: str, *, official: bool) -> str:
    url = _clean_url(url)
    if not url:
        raise MarketError(t("plugins.market.error.no_download"), kind="index")
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme in _REMOTE_SCHEMES and parsed.netloc:
        return url
    if official and (scheme in _LOCAL_SCHEMES or not scheme):
        # 官方源允许仓库内相对路径 / file://（离线安装）
        return url
    raise MarketError(t("plugins.market.error.bad_url", url=url), kind="index")


def parse_entry(raw: Any, *, source: MarketSource) -> MarketEntry:
    """解析并校验一条市场条目；不合法抛 :class:`MarketError`。"""
    if not isinstance(raw, dict):
        raise MarketError(t("plugins.market.error.bad_entry"), kind="index")
    plugin_id = str(raw.get("id") or "").strip()
    if not _PLUGIN_ID_RE.match(plugin_id):
        raise MarketError(t("plugins.market.error.bad_id", id=plugin_id), kind="index")

    version = str(raw.get("version") or "").strip()
    if not is_valid_version(version):
        raise MarketError(
            t("plugins.market.error.bad_version", id=plugin_id, version=version), kind="index"
        )
    version = normalize_version(version)

    sha256 = str(raw.get("sha256") or "").strip().lower()
    if not _SHA256_RE.match(sha256):
        raise MarketError(t("plugins.market.error.bad_sha256", id=plugin_id), kind="index")

    min_version = str(raw.get("min_app_version") or "").strip()
    if min_version and not is_valid_version(min_version):
        raise MarketError(
            t("plugins.market.error.bad_min_version", id=plugin_id, version=min_version), kind="index"
        )
    if min_version:
        min_version = normalize_version(min_version)

    icon = str(raw.get("icon") or "plugin").strip()
    if not _ICON_RE.match(icon):
        icon = "plugin"

    tags: list[str] = []
    raw_tags = raw.get("tags")
    if isinstance(raw_tags, (list, tuple)):
        for item in raw_tags:
            text = str(item or "").strip()
            if text and text not in tags:
                tags.append(text[:24])

    permissions: list[str] = []
    raw_permissions = raw.get("permissions")
    if isinstance(raw_permissions, (list, tuple)):
        for item in raw_permissions:
            text = str(item or "").strip()
            if text in ALLOWED_PERMISSIONS and text not in permissions:
                permissions.append(text)

    return MarketEntry(
        id=plugin_id,
        name=str(raw.get("name") or plugin_id).strip() or plugin_id,
        version=version,
        author=str(raw.get("author") or "").strip(),
        description=str(raw.get("description") or "").strip(),
        icon=icon,
        category=str(raw.get("category") or "").strip(),
        tags=tags,
        homepage=_clean_url(raw.get("homepage") or ""),
        download_url=_validate_download_url(
            str(raw.get("download_url") or ""), official=source.official
        ),
        sha256=sha256,
        size=max(0, _as_int(raw.get("size"), 0)),
        min_app_version=min_version,
        permissions=permissions,
        changelog=str(raw.get("changelog") or "").strip(),
        source_name=source.name,
        official=source.official,
    )


def parse_index(payload: Any, *, source: MarketSource) -> MarketIndex:
    """解析索引 JSON（schema v1）。顶层 ``plugins`` 必须是数组。"""
    if not isinstance(payload, dict):
        raise MarketError(t("plugins.market.error.bad_index"), kind="index")
    schema = _as_int(payload.get("schema"), 0)
    if schema != SCHEMA_VERSION:
        raise MarketError(
            t("plugins.market.error.bad_schema", schema=schema, expected=SCHEMA_VERSION), kind="index"
        )
    raw_entries = payload.get("plugins")
    if not isinstance(raw_entries, list):
        raise MarketError(t("plugins.market.error.bad_index"), kind="index")
    index = MarketIndex(
        source=source,
        name=str(payload.get("name") or source.name).strip(),
        updated=str(payload.get("updated") or "").strip(),
        fetched_at=time.time(),
    )
    seen: set[str] = set()
    for raw in raw_entries:
        try:
            entry = parse_entry(raw, source=source)
        except MarketError as exc:
            log.warning("market entry invalid (%s): %s", source.name, exc)
            continue
        if entry.id in seen:
            log.warning("market duplicate id skipped: %s (%s)", entry.id, source.name)
            continue
        seen.add(entry.id)
        index.entries.append(entry)
    return index


# ---------------------------------------------------------------------------
# 路径与 zip 安全
# ---------------------------------------------------------------------------


def _normalize_member(name: str) -> str:
    text = str(name or "").replace("\\", "/")
    if text.startswith("./"):
        text = text[2:]
    return text


def _member_parts(name: str) -> list[str]:
    text = _normalize_member(name)
    if not text:
        return []
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise MarketError(t("plugins.market.error.unsafe_zip"), kind="unsafe")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise MarketError(t("plugins.market.error.unsafe_zip"), kind="unsafe")
    return parts


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return bool(mode) and stat.S_ISLNK(mode)


def _validate_archive(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos = zf.infolist()
    if not infos:
        raise MarketError(t("plugins.market.error.empty_zip"), kind="unsafe")
    if len(infos) > MAX_ARCHIVE_FILES:
        raise MarketError(t("plugins.market.error.too_many_files"), kind="too_large")
    total = 0
    for info in infos:
        if info.flag_bits & 0x1:
            raise MarketError(t("plugins.market.error.encrypted_zip"), kind="unsafe")
        if _is_symlink(info):
            raise MarketError(t("plugins.market.error.symlink_zip"), kind="unsafe")
        _member_parts(info.filename)  # 只做路径校验
        if info.is_dir():
            continue
        total += int(info.file_size or 0)
        if total > MAX_UNCOMPRESSED_BYTES:
            raise MarketError(t("plugins.market.error.zip_too_large"), kind="too_large")
    return infos


def _top_level_dir(infos: list[zipfile.ZipInfo]) -> str:
    tops: set[str] = set()
    for info in infos:
        parts = _member_parts(info.filename)
        if not parts:
            continue
        tops.add(parts[0])
    if len(tops) != 1:
        raise MarketError(t("plugins.market.error.single_dir"), kind="unsafe")
    return next(iter(tops))


def safe_extract(zip_path: Path, dest_root: Path, *, plugin_id: str) -> Path:
    """把插件 zip 安全解压到 ``dest_root``，返回解压出的插件目录。

    只接受「单一顶层目录 + 该目录里有 plugin.json」的包结构。
    """
    zip_path = Path(zip_path)
    dest_root = Path(dest_root)
    dest_root.mkdir(parents=True, exist_ok=True)
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise MarketError(t("plugins.market.error.bad_zip", error=exc), kind="unsafe") from exc
    with zf:
        infos = _validate_archive(zf)
        top = _top_level_dir(infos)
        if not _PLUGIN_ID_RE.match(top):
            raise MarketError(t("plugins.market.error.single_dir"), kind="unsafe")
        target = (dest_root / top).resolve()
        if not _is_within(target, dest_root.resolve()):
            raise MarketError(t("plugins.market.error.unsafe_zip"), kind="unsafe")
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
        written = 0
        for info in infos:
            parts = _member_parts(info.filename)
            if len(parts) <= 1 and info.is_dir():
                continue
            if len(parts) <= 1:
                raise MarketError(t("plugins.market.error.single_dir"), kind="unsafe")
            relative = Path(*parts[1:])
            out_path = (target / relative).resolve()
            if not _is_within(out_path, target):
                raise MarketError(t("plugins.market.error.unsafe_zip"), kind="unsafe")
            if info.is_dir():
                out_path.mkdir(parents=True, exist_ok=True)
                continue
            out_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(info) as source, open(out_path, "wb") as sink:
                    while True:
                        chunk = source.read(65536)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > MAX_UNCOMPRESSED_BYTES:
                            raise MarketError(
                                t("plugins.market.error.zip_too_large"), kind="too_large"
                            )
                        sink.write(chunk)
            except MarketError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise MarketError(t("plugins.market.error.extract", error=exc), kind="unsafe") from exc
    if not (target / "plugin.json").is_file():
        raise MarketError(t("plugins.market.error.no_manifest"), kind="manifest")
    try:
        manifest = PluginManifest.load(target)
    except ManifestError as exc:
        raise MarketError(t("plugins.market.error.manifest", error=exc), kind="manifest") from exc
    if manifest.id != plugin_id:
        raise MarketError(
            t("plugins.market.error.id_mismatch", expected=plugin_id, actual=manifest.id),
            kind="manifest",
        )
    return target


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 256)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# 市场
# ---------------------------------------------------------------------------


def default_market_dir() -> Path:
    bundled = Path(config.BUNDLE) / "market"
    if bundled.is_dir():
        return bundled
    return Path(config.ROOT) / "market"


def default_state_path() -> Path:
    return Path(config.DATA_DIR) / "market_state.json"


def insecure_tls_enabled() -> bool:
    return os.environ.get("ENDBOX_INSECURE_TLS", "").strip().lower() in ("1", "true", "yes", "on")


class Marketplace:
    """插件市场：多来源索引 + 安装 / 更新 / 卸载。"""

    def __init__(
        self,
        *,
        target_dir: Path | str | None = None,
        market_dir: Path | str | None = None,
        settings: Any = None,
        state_path: Path | str | None = None,
        cache_dir: Path | str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        cache_ttl: float = DEFAULT_CACHE_TTL,
    ):
        self.target_dir = Path(target_dir) if target_dir is not None else Path(config.USER_PLUGINS_DIR)
        self.market_dir = Path(market_dir) if market_dir is not None else default_market_dir()
        self._settings = settings
        self._state_path = Path(state_path) if state_path is not None else default_state_path()
        self._cache_dir = (
            Path(cache_dir) if cache_dir is not None else (Path(config.CACHE_DIR) / "market")
        )
        self._transport = transport
        self._timeout = timeout
        self._cache_ttl = cache_ttl
        self._state: dict[str, InstalledRecord] = {}
        self._load_state()

    # -- 来源 ---------------------------------------------------------------
    def sources(self) -> list[MarketSource]:
        official = MarketSource(
            name=t("plugins.market.source.official", "官方源"), url="", official=True
        )
        result = [official]
        raw = self._get_setting(SETTINGS_SOURCES, [])
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "").strip()
                parsed = urlparse(url)
                if parsed.scheme not in _REMOTE_SCHEMES or not parsed.netloc:
                    continue
                result.append(
                    MarketSource(name=str(item.get("name") or url)[:48] or url, url=url)
                )
        return result

    def add_source(self, url: str, name: str | None = None) -> MarketSource:
        url = str(url or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in _REMOTE_SCHEMES or not parsed.netloc:
            raise MarketError(t("plugins.market.error.source_url"), kind="source")
        for source in self.sources():
            if source.url == url:
                raise MarketError(t("plugins.market.error.source_duplicate"), kind="source")
        source = MarketSource(name=(name or url).strip()[:48] or url, url=url)
        items = [
            {"name": item.name, "url": item.url}
            for item in self.sources()
            if not item.official
        ]
        items.append(source.to_dict())
        self._set_setting(SETTINGS_SOURCES, items)
        return source

    def remove_source(self, url: str) -> bool:
        url = str(url or "").strip()
        before = [item for item in self.sources() if not item.official]
        items = [
            {"name": item.name, "url": item.url}
            for item in before
            if item.url != url
        ]
        if len(items) == len(before):
            return False
        self._set_setting(SETTINGS_SOURCES, items)
        return True

    # -- 索引 ---------------------------------------------------------------
    def official_index(self) -> MarketIndex:
        source = MarketSource(
            name=t("plugins.market.source.official", "官方源"), url="", official=True
        )
        path = self.market_dir / "index.json"
        try:
            payload = json.loads(path.read_text("utf-8"))
        except FileNotFoundError as exc:
            raise MarketError(
                t("plugins.market.error.official_missing", path=path), kind="index"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise MarketError(
                t("plugins.market.error.bad_index_file", error=exc), kind="index"
            ) from exc
        return parse_index(payload, source=source)

    def load_catalog(self, *, force: bool = False) -> MarketCatalog:
        """加载官方源 + 用户添加的第三方源；单源失败只记录，不影响其他源。"""
        catalog = MarketCatalog()
        try:
            catalog.indexes.append(self.official_index())
        except MarketError as exc:
            catalog.errors.append(f"{t('plugins.market.source.official', '官方源')}：{exc}")
        for source in self.sources():
            if source.official:
                continue
            try:
                catalog.indexes.append(self.fetch_index(source, force=force))
            except MarketError as exc:
                catalog.errors.append(f"{source.name}：{exc}")
        return catalog

    def fetch_index(self, source: MarketSource, *, force: bool = False) -> MarketIndex:
        """拉取第三方索引：TTL 内用缓存；失败时退回旧缓存（离线降级）。"""
        if not force:
            cached = self._read_cached_index(source)
            if cached is not None:
                return cached
        try:
            payload = self._fetch_json(source.url)
            index = parse_index(payload, source=source)
            self._write_cached_index(source, payload)
            return index
        except MarketError:
            raise
        except Exception as exc:  # noqa: BLE001 - 网络/解析失败都降级
            cached = self._read_cached_index(source, max_age=0)
            if cached is not None:
                cached.cached = True
                cached.error = str(exc)
                log.warning("market source %s 拉取失败，使用缓存：%s", source.name, exc)
                return cached
            raise MarketError(
                t("plugins.market.source.failed", error=exc), kind="source"
            ) from exc

    def _fetch_json(self, url: str) -> Any:
        verify = not insecure_tls_enabled()
        client = httpx.Client(
            timeout=httpx.Timeout(self._timeout, connect=min(8.0, self._timeout)),
            follow_redirects=True,
            verify=verify,
            transport=self._transport,
            headers={
                "User-Agent": f"EndBox/{current_version()} (+plugin-market)",
                "Accept": "application/json",
            },
        )
        try:
            with client.stream("GET", url) as response:
                if response.status_code >= 400:
                    raise MarketError(
                        t("plugins.market.error.index_http", status=response.status_code),
                        kind="source",
                    )
                chunks = []
                size = 0
                for chunk in response.iter_bytes(65536):
                    size += len(chunk)
                    if size > MAX_INDEX_BYTES:
                        raise MarketError(t("plugins.market.error.index_too_large"), kind="too_large")
                    chunks.append(chunk)
                payload = b"".join(chunks)
        finally:
            client.close()
        try:
            return json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise MarketError(t("plugins.market.error.bad_index_json", error=exc), kind="source") from exc

    def _cache_file(self, source: MarketSource) -> Path:
        digest = hashlib.sha1(source.url.encode("utf-8")).hexdigest()[:16]
        return self._cache_dir / f"{digest}.json"

    def _read_cached_index(
        self, source: MarketSource, *, max_age: float | None = None
    ) -> MarketIndex | None:
        path = self._cache_file(source)
        try:
            raw = json.loads(path.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(raw, dict):
            return None
        fetched_at = float(raw.get("fetched_at") or 0)
        limit = self._cache_ttl if max_age is None else max_age
        if limit > 0 and time.time() - fetched_at > limit:
            return None
        try:
            index = parse_index(raw.get("index"), source=source)
        except MarketError:
            return None
        index.cached = True
        index.fetched_at = fetched_at
        return index

    def _write_cached_index(self, source: MarketSource, payload: Any) -> None:
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            path = self._cache_file(source)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(
                json.dumps({"fetched_at": time.time(), "index": payload}, ensure_ascii=False),
                "utf-8",
            )
            os.replace(tmp, path)
        except Exception:  # noqa: BLE001 - 缓存失败不影响使用
            log.warning("market index cache write failed: %s", source.url)

    # -- 安装状态 -----------------------------------------------------------
    def installed_state(self) -> dict[str, InstalledRecord]:
        return dict(self._state)

    def installed_record(self, plugin_id: str) -> InstalledRecord | None:
        return self._state.get(plugin_id)

    def installed_version(self, plugin_id: str) -> str | None:
        """优先安装记录，其次读用户插件目录里的 manifest。"""
        record = self._state.get(plugin_id)
        if record is not None:
            return record.version
        manifest = self._target_manifest(plugin_id)
        return manifest.version if manifest is not None else None

    def origin_of(self, plugin_id: str) -> str:
        """``market`` / ``local`` / ``bundled``。"""
        record = self._state.get(plugin_id)
        if record is not None:
            return record.source
        if self._target_manifest(plugin_id) is not None:
            return "local"
        bundled = Path(config.PLUGINS_DIR) / plugin_id
        if (bundled / "plugin.json").is_file():
            return "bundled"
        return "local"

    def _target_manifest(self, plugin_id: str) -> PluginManifest | None:
        if not _PLUGIN_ID_RE.match(str(plugin_id or "")):
            return None
        try:
            return PluginManifest.load(self.target_dir / plugin_id)
        except Exception:  # noqa: BLE001 - 坏 manifest 当作没装
            return None

    def _load_state(self) -> None:
        try:
            raw = json.loads(self._state_path.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            self._state = {}
            return
        records: dict[str, InstalledRecord] = {}
        plugins = raw.get("plugins") if isinstance(raw, dict) else None
        if isinstance(plugins, dict):
            for plugin_id, item in plugins.items():
                if not isinstance(item, dict):
                    continue
                item = dict(item)
                item.setdefault("plugin_id", plugin_id)
                record = InstalledRecord.from_dict(item)
                if record.plugin_id:
                    records[record.plugin_id] = record
        self._state = records

    def _save_state(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema": SCHEMA_VERSION,
                "plugins": {
                    plugin_id: record.to_dict() for plugin_id, record in sorted(self._state.items())
                },
            }
            tmp = self._state_path.with_name(self._state_path.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
            os.replace(tmp, self._state_path)
        except Exception:  # noqa: BLE001
            log.warning("market state write failed: %s", self._state_path)

    def _remember_install(self, entry: MarketEntry, manifest: PluginManifest) -> None:
        self._state[entry.id] = InstalledRecord(
            plugin_id=entry.id,
            version=manifest.version,
            source="market",
            source_name=entry.source_name,
            installed_at=time.time(),
            sha256=entry.sha256,
        )
        self._save_state()

    def _forget_install(self, plugin_id: str) -> None:
        if self._state.pop(plugin_id, None) is not None:
            self._save_state()

    # -- 下载 ---------------------------------------------------------------
    def resolve_download_url(self, entry: MarketEntry) -> str:
        """把条目里的下载地址解析成可直接请求的 URL / 本地路径。"""
        url = entry.download_url
        scheme = urlparse(url).scheme.lower()
        if scheme in _REMOTE_SCHEMES:
            return url
        if scheme in _LOCAL_SCHEMES:
            return url
        # 官方源相对路径：相对仓库 / 打包目录里的 market/
        candidate = Path(url)
        if not candidate.is_absolute():
            candidate = self.market_dir / candidate
        if not candidate.is_file():
            raise MarketError(t("plugins.market.error.no_download"), kind="download")
        return candidate.as_uri()

    def download_package(
        self,
        entry: MarketEntry,
        dest: Path,
        *,
        progress: Callable[[int, int], None] | None = None,
    ) -> Path:
        """下载（或复制本地）插件包到 ``dest``，校验大小与 sha256。"""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        url = self.resolve_download_url(entry)
        scheme = urlparse(url).scheme.lower()
        digest = hashlib.sha256()
        written = 0
        if scheme in _LOCAL_SCHEMES:
            source_path = Path(url2pathname(urlparse(url).path))
            try:
                size = source_path.stat().st_size
            except OSError as exc:
                raise MarketError(t("plugins.market.error.no_download"), kind="download") from exc
            if size > MAX_PACKAGE_BYTES:
                raise MarketError(t("plugins.market.error.too_large"), kind="too_large")
            try:
                with open(source_path, "rb") as source, open(dest, "wb") as sink:
                    while True:
                        chunk = source.read(1024 * 256)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > MAX_PACKAGE_BYTES:
                            raise MarketError(t("plugins.market.error.too_large"), kind="too_large")
                        digest.update(chunk)
                        sink.write(chunk)
                        if progress is not None:
                            progress(written, size)
            except MarketError:
                raise
            except OSError as exc:
                raise MarketError(t("plugins.market.error.download_failed", error=exc), kind="download") from exc
        else:
            verify = not insecure_tls_enabled()
            client = httpx.Client(
                timeout=httpx.Timeout(self._timeout, connect=min(8.0, self._timeout)),
                follow_redirects=True,
                verify=verify,
                transport=self._transport,
                headers={
                    "User-Agent": f"EndBox/{current_version()} (+plugin-market)",
                    "Accept": "application/zip, application/octet-stream, */*",
                },
            )
            try:
                with client.stream("GET", url) as response:
                    if response.status_code >= 400:
                        raise MarketError(
                            t("plugins.market.error.download_http", status=response.status_code),
                            kind="download",
                        )
                    total = _as_int(response.headers.get("content-length"), 0)
                    if total > MAX_PACKAGE_BYTES:
                        raise MarketError(t("plugins.market.error.too_large"), kind="too_large")
                    with open(dest, "wb") as sink:
                        for chunk in response.iter_bytes(65536):
                            written += len(chunk)
                            if written > MAX_PACKAGE_BYTES:
                                raise MarketError(t("plugins.market.error.too_large"), kind="too_large")
                            digest.update(chunk)
                            sink.write(chunk)
                            if progress is not None:
                                progress(written, total)
            except MarketError:
                raise
            except httpx.TimeoutException as exc:
                raise MarketError(
                    t("plugins.market.error.download_timeout"), kind="timeout"
                ) from exc
            except httpx.HTTPError as exc:
                raise MarketError(
                    t("plugins.market.error.download_failed", error=exc), kind="download"
                ) from exc
            finally:
                client.close()

        actual = digest.hexdigest()
        if actual.lower() != entry.sha256.lower():
            self._remove_quietly(dest)
            raise MarketError(t("plugins.market.error.sha256"), kind="sha256")
        return dest

    # -- 安装 / 卸载 --------------------------------------------------------
    def install(
        self,
        entry: MarketEntry,
        *,
        progress: Callable[[int, int], None] | None = None,
    ) -> InstallResult:
        """下载并安装 / 覆盖安装。返回 ``InstallResult``（不抛异常）。"""
        try:
            if not entry.compatible():
                raise MarketError(
                    t(
                        "plugins.market.error.min_version",
                        version=entry.min_app_version,
                        current=current_version(),
                    ),
                    kind="incompatible",
                )
            self.target_dir.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix="endbox_market_"))
            try:
                package = self.download_package(entry, staging / f"{entry.id}.zip", progress=progress)
                extracted = safe_extract(package, staging / "extract", plugin_id=entry.id)
                manifest = PluginManifest.load(extracted)
                if compare_versions(manifest.version, entry.version) != 0:
                    raise MarketError(
                        t(
                            "plugins.market.error.version_mismatch",
                            expected=entry.version,
                            actual=manifest.version,
                        ),
                        kind="manifest",
                    )
                target = self.target_dir / entry.id
                replaced = target.exists()
                self._swap_in(extracted, target)
                self._remember_install(entry, manifest)
                log.info(
                    "plugin installed from market: %s v%s (%s, %s bytes)",
                    entry.id,
                    manifest.version,
                    entry.source_name,
                    entry.size,
                )
                return InstallResult(
                    ok=True,
                    plugin_id=entry.id,
                    version=manifest.version,
                    path=target,
                    replaced=replaced,
                )
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        except MarketError as exc:
            log.warning("plugin install failed: %s: %s", entry.id, exc)
            return InstallResult(ok=False, plugin_id=entry.id, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - 任何意外都变成可读失败
            log.exception("plugin install crashed: %s", entry.id)
            return InstallResult(
                ok=False,
                plugin_id=entry.id,
                error=t("plugins.market.error.install", error=f"{type(exc).__name__}: {exc}"),
            )

    def _swap_in(self, extracted: Path, target: Path) -> None:
        """把解压好的目录换进目标位置；失败时回滚旧目录。"""
        backup = target.with_name(target.name + ".market_old")
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        if target.exists():
            try:
                os.replace(target, backup)
            except OSError:
                shutil.copytree(target, backup)
                shutil.rmtree(target)
        try:
            shutil.move(str(extracted), str(target))
        except Exception:
            self._remove_quietly(target)
            if backup.exists():
                try:
                    os.replace(backup, target)
                except OSError:
                    shutil.copytree(backup, target)
                    shutil.rmtree(backup, ignore_errors=True)
            raise
        self._remove_quietly(backup)

    def uninstall(self, plugin_id: str) -> bool:
        """删除用户插件目录并清掉来源记录；随程序分发的插件不允许卸载。"""
        plugin_id = str(plugin_id or "").strip()
        if not _PLUGIN_ID_RE.match(plugin_id):
            raise MarketError(t("plugins.market.error.bad_id", id=plugin_id), kind="manifest")
        target = self.target_dir / plugin_id
        if not target.exists():
            bundled = Path(config.PLUGINS_DIR) / plugin_id
            if (bundled / "plugin.json").is_file():
                raise MarketError(t("plugins.market.error.bundled"), kind="bundled")
            raise MarketError(t("plugins.market.error.not_installed"), kind="missing")
        shutil.rmtree(target)
        self._forget_install(plugin_id)
        log.info("plugin uninstalled: %s", plugin_id)
        return True

    # -- 工具 ---------------------------------------------------------------
    @staticmethod
    def _remove_quietly(path: Path) -> None:
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    def _get_setting(self, path: str, default: Any) -> Any:
        if self._settings is None:
            return default
        try:
            return self._settings.get(path, default)
        except Exception:  # noqa: BLE001
            return default

    def _set_setting(self, path: str, value: Any) -> None:
        if self._settings is None:
            return
        try:
            self._settings.set(path, value)
        except Exception:  # noqa: BLE001
            log.exception("market setting save failed: %s", path)


__all__ = [
    "DEFAULT_CACHE_TTL",
    "MAX_ARCHIVE_FILES",
    "MAX_INDEX_BYTES",
    "MAX_PACKAGE_BYTES",
    "MAX_UNCOMPRESSED_BYTES",
    "SCHEMA_VERSION",
    "InstallResult",
    "InstalledRecord",
    "MarketCatalog",
    "MarketEntry",
    "MarketError",
    "MarketIndex",
    "MarketSource",
    "Marketplace",
    "default_market_dir",
    "default_state_path",
    "parse_entry",
    "parse_index",
    "safe_extract",
    "sha256_bytes",
    "sha256_file",
]
