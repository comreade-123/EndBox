# -*- coding: utf-8 -*-
"""更新检查服务：双来源（GitHub Releases / version.json）+ 缓存 + 忽略版本。

纯逻辑、不依赖 Qt；设置页在线程池里调用 :meth:`UpdateService.check`，
结果回到 UI 线程渲染。任何网络失败都只返回 ``ok=False`` 的可读错误，
不抛异常、不阻塞、不弹系统错误框（「静默降级」）。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from endbox.core.log import get_logger
from endbox.core.text import t
from endbox.modules.update import feed, net
from endbox.modules.update.net import DEFAULT_TIMEOUT, FetchError
from endbox.version import compare_versions, current_version

log = get_logger("update.service")

#: 两次自动检查之间的最小间隔（秒）；手动点「检查更新」不受限制
DEFAULT_INTERVAL = 6 * 3600


@dataclass
class CheckResult:
    """一次检查的结果（可直接缓存在 data/cache/update_check.json）。"""

    ok: bool
    current: str = ""
    info: feed.ReleaseInfo | None = None
    ignored: bool = False
    degraded: bool = False
    cached: bool = False
    error: str | None = None
    checked_at: float = 0.0

    @property
    def latest_version(self) -> str:
        return self.info.version if self.info is not None else self.current

    @property
    def has_update(self) -> bool:
        """有更新且没有被用户忽略。"""
        return bool(self.ok and self.info is not None and self.info.is_newer_than(self.current) and not self.ignored)

    @property
    def html_url(self) -> str:
        if self.info is not None and self.info.html_url:
            return self.info.html_url
        return feed.RELEASES_PAGE

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "current": self.current,
            "info": self.info.to_dict() if self.info is not None else None,
            "ignored": self.ignored,
            "degraded": self.degraded,
            "cached": self.cached,
            "error": self.error,
            "checked_at": self.checked_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CheckResult":
        info_raw = raw.get("info")
        info = feed.ReleaseInfo.from_dict(info_raw) if isinstance(info_raw, dict) else None
        return cls(
            ok=bool(raw.get("ok") and info is not None),
            current=str(raw.get("current") or current_version()),
            info=info,
            ignored=bool(raw.get("ignored")),
            degraded=bool(raw.get("degraded")),
            cached=bool(raw.get("cached")),
            error=str(raw["error"]) if raw.get("error") else None,
            checked_at=float(raw.get("checked_at") or 0.0),
        )


FetchPayload = Callable[[], Any]


def update_check_disabled() -> bool:
    """开发 / 测试环境关闭自动检查（截图、集成测试不联网）。"""
    return os.environ.get("ENDBOX_NO_UPDATE_CHECK", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


class UpdateService:
    """检查更新。``create_service(ctx)`` 由 app.py 可选装配，设置页也能直接构造。"""

    SETTINGS_IGNORED = "update.ignored_version"
    SETTINGS_LAST_CHECK = "update.last_check"

    def __init__(
        self,
        *,
        settings: Any = None,
        current: str | None = None,
        cache_path: Path | str | None = None,
        github_url: str = feed.GITHUB_RELEASES_API,
        version_url: str = feed.VERSION_JSON_URL,
        github_fetcher: FetchPayload | None = None,
        version_fetcher: FetchPayload | None = None,
        transport: Any = None,
        timeout: float = DEFAULT_TIMEOUT,
        interval_seconds: float = DEFAULT_INTERVAL,
        clock: Callable[[], float] = time.time,
    ):
        self._settings = settings
        self._current = current
        self._cache_path = Path(cache_path) if cache_path is not None else _default_cache_path()
        self._github_url = github_url
        self._version_url = version_url
        self._timeout = timeout
        self._transport = transport
        self._github_fetcher = github_fetcher or self._fetch_github
        self._version_fetcher = version_fetcher or self._fetch_version_json
        self._interval = float(interval_seconds)
        self._clock = clock
        self._notes: list[str] = []

    # -- 基本信息 -----------------------------------------------------------
    @property
    def current_version(self) -> str:
        return self._current or current_version()

    @property
    def cache_path(self) -> Path:
        return self._cache_path

    @property
    def interval_seconds(self) -> float:
        return self._interval

    def last_check_at(self) -> float:
        """上次检查时间：设置里的值优先，其次缓存里的时间。"""
        value = self._get_setting(self.SETTINGS_LAST_CHECK, 0)
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    # -- 忽略版本 -----------------------------------------------------------
    def ignored_version(self) -> str:
        return str(self._get_setting(self.SETTINGS_IGNORED, "") or "")

    def is_ignored(self, version: str) -> bool:
        ignored = self.ignored_version()
        return bool(ignored) and compare_versions(ignored, version) == 0

    def ignore(self, version: str) -> None:
        version = str(version or "").strip()
        if not version:
            return
        self._set_setting(self.SETTINGS_IGNORED, version)
        log.info("忽略版本：%s", version)

    def unignore(self) -> None:
        self._set_setting(self.SETTINGS_IGNORED, "")
        log.info("取消忽略版本")

    # -- 缓存 ---------------------------------------------------------------
    def cached_result(self) -> CheckResult | None:
        try:
            raw = json.loads(self._cache_path.read_text("utf-8"))
        except FileNotFoundError:
            return None
        except Exception:  # noqa: BLE001 - 缓存损坏就当没有
            log.warning("更新缓存不可读：%s", self._cache_path)
            return None
        if not isinstance(raw, dict):
            return None
        try:
            result = CheckResult.from_dict(raw)
        except Exception:  # noqa: BLE001
            return None
        # 缓存可能是旧版本程序写的：当前版本与忽略状态以本次运行为准
        result.current = self.current_version
        if result.info is not None:
            result.ignored = self.is_ignored(result.info.version)
        return result

    def _write_cache(self, result: CheckResult) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
            tmp = self._cache_path.with_name(self._cache_path.name + ".tmp")
            tmp.write_text(payload, "utf-8")
            os.replace(tmp, self._cache_path)
        except Exception:  # noqa: BLE001 - 缓存失败不影响检查
            log.warning("更新缓存写入失败：%s", self._cache_path)

    # -- 检查 ---------------------------------------------------------------
    def check(self, force: bool = True) -> CheckResult:
        """同步检查（调用方负责放到后台线程）。永不抛异常。

        ``force=False`` 时若缓存还新鲜（默认 6 小时内），直接返回缓存，
        用于「顶栏提示」这类自动检查；设置页手动检查传 ``force=True``。
        """
        now = self._clock()
        if update_check_disabled():
            cached = self.cached_result()
            if cached is not None:
                cached.cached = True
                return cached
            return CheckResult(
                ok=False,
                current=self.current_version,
                error=t("update.error.disabled"),
                checked_at=now,
            )
        if not force and self._interval > 0:
            cached = self.cached_result()
            if cached is not None and cached.checked_at and now - cached.checked_at < self._interval:
                cached.cached = True
                return cached

        current = self.current_version
        errors: list[str] = []
        info: feed.ReleaseInfo | None = None
        degraded = False

        # 1) GitHub Releases（发布后自动生效）
        try:
            payload = self._github_fetcher()
            releases = feed.parse_github_releases(payload)
            info = feed.pick_latest_release(releases)
            if info is None:
                errors.append(t("update.error.no_releases"))
        except FetchError as exc:
            errors.append(str(exc))
        except Exception as exc:  # noqa: BLE001 - 解析异常同样降级
            log.warning("GitHub release 拉取失败：%s", exc)
            errors.append(t("update.error.unknown", error=f"{type(exc).__name__}: {exc}"))

        # 2) 仓库内 version.json（GitHub 不可用时的兜底）
        if info is None:
            try:
                info = feed.parse_version_payload(self._version_fetcher())
                degraded = True
            except FetchError as exc:
                errors.append(str(exc))
            except Exception as exc:  # noqa: BLE001
                log.warning("version.json 拉取失败：%s", exc)
                errors.append(t("update.error.unknown", error=f"{type(exc).__name__}: {exc}"))

        # 3) 都失败 → 上次成功的缓存（离线时仍能显示）
        if info is None:
            cached = self.cached_result()
            if cached is not None and cached.info is not None and cached.ok:
                cached.cached = True
                cached.degraded = True
                cached.current = current
                cached.ignored = self.is_ignored(cached.info.version)
                cached.error = self._combine(errors)
                cached.checked_at = now
                return cached
            return CheckResult(
                ok=False,
                current=current,
                error=self._combine(errors) or t("update.error.no_releases"),
                checked_at=now,
            )

        result = CheckResult(
            ok=True,
            current=current,
            info=info,
            ignored=self.is_ignored(info.version),
            degraded=degraded,
            checked_at=now,
        )
        self._write_cache(result)
        self._set_setting(self.SETTINGS_LAST_CHECK, float(now))
        if result.has_update:
            log.info("发现新版本：%s -> %s（来源 %s）", current, info.version, info.source)
        else:
            log.info("已是最新版本：%s（来源 %s）", current, info.source)
        return result

    # -- 默认取数 -----------------------------------------------------------
    def _fetch_github(self) -> Any:
        return net.github_releases(
            self._github_url, timeout=self._timeout, transport=self._transport
        )

    def _fetch_version_json(self) -> Any:
        return net.get_json(self._version_url, timeout=self._timeout, transport=self._transport)

    # -- 设置读写 -----------------------------------------------------------
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
        except Exception:  # noqa: BLE001 - 设置失败不影响检查
            log.exception("更新设置保存失败：%s", path)

    @staticmethod
    def _combine(errors: list[str]) -> str:
        unique: list[str] = []
        for error in errors:
            text = str(error or "").strip()
            if text and text not in unique:
                unique.append(text)
        if not unique:
            return ""
        return t("update.error.failed", error="；".join(unique))


def _default_cache_path() -> Path:
    from endbox import config

    return config.CACHE_DIR / "update_check.json"


def create_service(ctx: Any) -> UpdateService:
    """app.py 可选装配：``("update", "endbox.modules.update")``。"""
    return UpdateService(settings=getattr(ctx, "settings", None))


__all__ = [
    "CheckResult",
    "DEFAULT_INTERVAL",
    "UpdateService",
    "create_service",
    "update_check_disabled",
]
