# -*- coding: utf-8 -*-
"""版本更新逻辑测试：语义化比较、两种 payload、CHANGELOG、降级、忽略版本持久化。

全部离线：网络层用 httpx.MockTransport，服务层注入假 fetcher，不碰真实网络。

    python run_tests.py tests/test_update.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from endbox.config import Settings
from endbox.modules.update import feed
from endbox.modules.update.net import FetchError, get_json, request_text
from endbox.modules.update.service import CheckResult, UpdateService
from endbox.version import (
    __version__,
    compare_versions,
    current_version,
    is_newer,
    is_valid_version,
    normalize_version,
    version_key,
    version_tuple,
)


@pytest.fixture(autouse=True)
def _enable_update_checks(monkeypatch):
    """测试默认开启检查（conftest 为截图/集成测试关掉了网络检查）。"""
    monkeypatch.delenv("ENDBOX_NO_UPDATE_CHECK", raising=False)


def test_update_check_disabled_by_env(monkeypatch, tmp_path):
    """ENDBOX_NO_UPDATE_CHECK=1 时静默返回，不发网络请求。"""
    from endbox.modules.update.service import UpdateService as _Service

    monkeypatch.setenv("ENDBOX_NO_UPDATE_CHECK", "1")
    calls = []

    def _boom():
        calls.append(1)
        raise AssertionError("不应发起网络请求")

    service = _Service(
        settings=Settings(tmp_path / "s.json"),
        cache_path=tmp_path / "cache.json",
        github_fetcher=_boom,
        version_fetcher=_boom,
    )
    result = service.check(force=True)
    assert not result.ok
    assert calls == []
    assert result.error

# ---------------------------------------------------------------------------
# 版本源与语义化比较
# ---------------------------------------------------------------------------


def test_single_version_source_is_1_1_0():
    """版本唯一来源在 endbox/version.py；1.0.0 -> 1.1.0。"""
    assert __version__ == "1.1.0"
    assert current_version() == __version__


def test_current_version_env_override(monkeypatch):
    monkeypatch.setenv("ENDBOX_VERSION", "v1.2")
    assert current_version() == "1.2.0"
    monkeypatch.setenv("ENDBOX_VERSION", "not-a-version")
    assert current_version() == __version__


def test_version_parse_and_normalize():
    assert normalize_version("v1.2") == "1.2.0"
    assert normalize_version(" 1.2.3 ") == "1.2.3"
    assert normalize_version("1.2.3-beta.1") == "1.2.3-beta.1"
    assert version_tuple("v2.3") == (2, 3, 0)
    assert version_tuple("garbage") == (0, 0, 0)
    assert is_valid_version("v1.1.0")
    assert not is_valid_version("main")
    assert not is_valid_version("")


def test_compare_versions():
    assert compare_versions("1.1.0", "1.0.0") == 1
    assert compare_versions("1.0.0", "1.1.0") == -1
    assert compare_versions("v1.1.0", "1.1") == 0
    assert compare_versions("1.1.0-beta", "1.1.0") == -1
    assert is_newer("1.1.0", "1.0.9")
    assert not is_newer("1.0.0", "1.0.0")
    assert version_key("1.1.0") > version_key("1.1.0-beta")
    assert version_key("1.2.0") < version_key("1.9.0")


# ---------------------------------------------------------------------------
# GitHub Releases payload
# ---------------------------------------------------------------------------


def _release(tag: str, *, draft: bool = False, prerelease: bool = False, body: str = "说明") -> dict:
    return {
        "tag_name": tag,
        "name": f"EndBox {tag}",
        "body": body,
        "draft": draft,
        "prerelease": prerelease,
        "published_at": "2026-11-14T08:00:00Z",
        "html_url": f"https://github.com/comreade-123/EndBox/releases/tag/{tag}",
        "assets": [
            {
                "name": "EndBox.exe",
                "browser_download_url": f"https://example.com/{tag}/EndBox.exe",
                "size": 12345678,
            },
            {
                "name": "EndBox-notes.md",
                "browser_download_url": f"https://example.com/{tag}/notes.md",
                "size": 10,
            },
        ],
    }


def test_parse_github_release_and_pick_download():
    info = feed.parse_github_release(_release("v1.1.0"))
    assert info is not None
    assert info.version == "1.1.0"
    assert info.source == feed.SOURCE_GITHUB
    assert info.notes == "说明"
    assert info.download_url == ""
    url, kind = info.best_download()
    assert kind == "exe"
    assert url.endswith("EndBox.exe")


def test_parse_github_releases_skips_draft_and_prefers_stable():
    payload = [
        _release("v1.2.0-beta.1", prerelease=True),
        _release("v1.2.0", draft=True),
        _release("v1.1.0"),
        _release("v1.0.0"),
    ]
    releases = feed.parse_github_releases(payload)
    assert [item.version for item in releases] == ["1.2.0-beta.1", "1.1.0", "1.0.0"]
    latest = feed.pick_latest_release(releases)
    assert latest is not None and latest.version == "1.1.0"


def test_parse_github_release_without_valid_tag_returns_none():
    assert feed.parse_github_release({"tag_name": "nightly", "draft": False}) is None
    assert feed.parse_github_release({"name": "no tag"}) is None
    assert feed.parse_github_release(_release("v1.1.0", draft=True)) is None


def test_parse_github_releases_rejects_bad_payload():
    with pytest.raises(ValueError):
        feed.parse_github_releases("not-a-list")


# ---------------------------------------------------------------------------
# version.json payload
# ---------------------------------------------------------------------------


def _version_payload() -> dict:
    return {
        "schema": 1,
        "version": "1.1.0",
        "released": "2026-11-14",
        "title": "v1.1.0 · 插件市场与版本更新",
        "notes": ["新增插件市场", "新增更新检查"],
        "downloads": {
            "exe": "https://github.com/comreade-123/EndBox/releases/download/v1.1.0/EndBox.exe",
            "zip": "https://github.com/comreade-123/EndBox/releases/download/v1.1.0/EndBox-v1.1.0.zip",
        },
        "html_url": "https://github.com/comreade-123/EndBox/releases/tag/v1.1.0",
    }


def test_parse_version_payload():
    info = feed.parse_version_payload(_version_payload())
    assert info.version == "1.1.0"
    assert info.source == feed.SOURCE_VERSION_JSON
    assert info.published_at == "2026-11-14"
    assert "新增插件市场" in info.notes
    url, kind = info.best_download()
    assert kind == "exe" and url.endswith("EndBox.exe")


def test_parse_version_payload_variants():
    payload = {
        "version": "v1.2",
        "download_url": "https://example.com/EndBox.exe",
        "notes": "单段说明",
    }
    info = feed.parse_version_payload(payload)
    assert info.version == "1.2.0"
    assert info.download_kind == "exe"
    assert info.notes == "单段说明"
    assert info.best_download()[0] == "https://example.com/EndBox.exe"

    payload = {
        "version": "1.2.0",
        "assets": [{"name": "pkg.zip", "url": "https://example.com/pkg.zip", "size": 10}],
    }
    info = feed.parse_version_payload(payload)
    assert info.best_download()[1] == "zip"


def test_parse_version_payload_rejects_bad_input():
    with pytest.raises(ValueError):
        feed.parse_version_payload({"version": "latest"})
    with pytest.raises(ValueError):
        feed.parse_version_payload([])


# ---------------------------------------------------------------------------
# CHANGELOG
# ---------------------------------------------------------------------------

CHANGELOG = """# 更新日志

## [1.1.0] - 2026-11-14

### 新功能
- 插件市场
- 版本更新

## [1.0.0] - 2026-11-13

- 首个版本
"""


def test_parse_changelog():
    entries = feed.parse_changelog(CHANGELOG)
    assert [item["version"] for item in entries] == ["1.1.0", "1.0.0"]
    assert entries[0]["date"] == "2026-11-14"
    assert "插件市场" in entries[0]["body"]
    assert "首个版本" in entries[1]["body"]
    assert feed.changelog_notes(CHANGELOG, "v1.0.0").strip().startswith("- 首个版本")
    assert feed.changelog_notes(CHANGELOG, "9.9.9") == ""


# ---------------------------------------------------------------------------
# UpdateService：双来源 / 降级 / 缓存 / 忽略版本
# ---------------------------------------------------------------------------


def _service(tmp_path: Path, settings: Settings, **kwargs) -> UpdateService:
    kwargs.setdefault("current", "1.0.0")
    kwargs.setdefault("cache_path", tmp_path / "cache" / "update_check.json")
    kwargs.setdefault("interval_seconds", 0)
    return UpdateService(settings=settings, **kwargs)


def test_check_github_success_and_cache(tmp_path):
    settings = Settings(tmp_path / "settings.json")
    service = _service(
        tmp_path,
        settings,
        github_fetcher=lambda: [_release("v1.1.0")],
        version_fetcher=lambda: _version_payload(),
    )
    result = service.check()
    assert result.ok
    assert result.info is not None
    assert result.info.version == "1.1.0"
    assert result.info.source == feed.SOURCE_GITHUB
    assert result.has_update
    assert not result.degraded
    assert not result.ignored
    assert service.cached_result() is not None
    assert service.last_check_at() > 0


def test_check_up_to_date(tmp_path):
    settings = Settings(tmp_path / "settings.json")
    service = _service(
        tmp_path,
        settings,
        current="1.1.0",
        github_fetcher=lambda: [_release("v1.1.0")],
    )
    result = service.check()
    assert result.ok
    assert not result.has_update
    assert result.latest_version == "1.1.0"


def test_check_falls_back_to_version_json(tmp_path):
    settings = Settings(tmp_path / "settings.json")

    def _boom():
        raise FetchError("限流", kind="ratelimit")

    service = _service(
        tmp_path,
        settings,
        github_fetcher=_boom,
        version_fetcher=lambda: _version_payload(),
    )
    result = service.check()
    assert result.ok
    assert result.degraded
    assert result.info is not None and result.info.source == feed.SOURCE_VERSION_JSON
    assert result.has_update


def test_check_offline_uses_cache(tmp_path):
    settings = Settings(tmp_path / "settings.json")
    good = _service(
        tmp_path,
        settings,
        github_fetcher=lambda: [_release("v1.1.0")],
        version_fetcher=lambda: _version_payload(),
    )
    assert good.check().has_update

    def _boom():
        raise FetchError("断网", kind="offline")

    offline = _service(
        tmp_path,
        settings,
        current="1.0.0",
        github_fetcher=_boom,
        version_fetcher=_boom,
    )
    result = offline.check(force=True)
    assert result.ok
    assert result.cached
    assert result.info is not None and result.info.version == "1.1.0"
    assert result.has_update


def test_check_offline_without_cache_returns_error(tmp_path):
    settings = Settings(tmp_path / "settings.json")

    def _boom():
        raise FetchError("网络不可用，暂时无法检查更新。", kind="offline")

    service = _service(tmp_path, settings, github_fetcher=_boom, version_fetcher=_boom)
    result = service.check()
    assert not result.ok
    assert result.info is None
    assert result.error and "网络不可用" in result.error
    assert not result.has_update


def test_check_never_raises_on_unexpected_error(tmp_path):
    settings = Settings(tmp_path / "settings.json")

    def _boom():
        raise RuntimeError("unexpected")

    service = _service(tmp_path, settings, github_fetcher=_boom, version_fetcher=_boom)
    result = service.check()
    assert not result.ok
    assert result.error


def test_ignore_version_persists(tmp_path):
    settings_file = tmp_path / "settings.json"
    settings = Settings(settings_file)
    kwargs = dict(
        current="1.0.0",
        github_fetcher=lambda: [_release("v1.1.0")],
        version_fetcher=lambda: _version_payload(),
    )
    service = _service(tmp_path, settings, **kwargs)
    result = service.check()
    assert result.has_update

    service.ignore("1.1.0")
    assert service.is_ignored("1.1.0")
    assert service.is_ignored("v1.1.0")
    assert not service.is_ignored("1.2.0")

    # 重新读设置文件：忽略状态持久化
    again = Settings(settings_file)
    reloaded = _service(tmp_path, again, **kwargs)
    assert reloaded.ignored_version() == "1.1.0"
    result = reloaded.check()
    assert result.ok and result.ignored and not result.has_update
    assert result.info is not None

    reloaded.unignore()
    assert reloaded.ignored_version() == ""
    assert reloaded.check().has_update


def test_fresh_cache_used_without_calling_fetchers(tmp_path):
    settings = Settings(tmp_path / "settings.json")
    calls = {"github": 0, "version": 0}

    def _github():
        calls["github"] += 1
        return [_release("v1.1.0")]

    def _version():
        calls["version"] += 1
        return _version_payload()

    service = UpdateService(
        settings=settings,
        current="1.0.0",
        cache_path=tmp_path / "cache" / "update_check.json",
        github_fetcher=_github,
        version_fetcher=_version,
        interval_seconds=3600,
    )
    assert service.check(force=True).has_update
    assert calls == {"github": 1, "version": 0}

    cached = service.check(force=False)
    assert cached.cached
    assert calls == {"github": 1, "version": 0}

    assert service.check(force=True).has_update
    assert calls == {"github": 2, "version": 0}


def test_cached_result_uses_current_runtime_version(tmp_path):
    """程序升级后，旧缓存里的 current 不能把新版本误报成有更新。"""
    settings = Settings(tmp_path / "settings.json")
    cache_path = tmp_path / "cache" / "update_check.json"
    old = UpdateService(
        settings=settings,
        current="1.0.0",
        cache_path=cache_path,
        interval_seconds=0,
        github_fetcher=lambda: [_release("v1.1.0")],
    )
    assert old.check(force=True).has_update

    upgraded = UpdateService(
        settings=settings,
        current="1.1.0",
        cache_path=cache_path,
        interval_seconds=0,
        github_fetcher=lambda: [_release("v1.1.0")],
    )
    cached = upgraded.cached_result()
    assert cached is not None and cached.current == "1.1.0"
    assert not cached.has_update


def test_check_result_cache_roundtrip(tmp_path):
    settings = Settings(tmp_path / "settings.json")
    service = _service(
        tmp_path,
        settings,
        github_fetcher=lambda: [_release("v1.1.0")],
    )
    result = service.check()
    payload = json.loads(json.dumps(result.to_dict()))
    restored = CheckResult.from_dict(payload)
    assert restored.ok
    assert restored.info is not None and restored.info.version == "1.1.0"
    assert restored.has_update
    assert CheckResult.from_dict({"ok": True, "info": None}).ok is False


# ---------------------------------------------------------------------------
# 网络层错误分类（MockTransport，不联网）
# ---------------------------------------------------------------------------


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_net_ratelimit_detected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, json={"message": "rate"})

    with pytest.raises(FetchError) as info:
        get_json("https://api.github.com/x", transport=_transport(handler))
    assert info.value.kind == "ratelimit"


def test_net_http_error_detected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(FetchError) as info:
        get_json("https://example.com/x", transport=_transport(handler))
    assert info.value.kind == "http"
    assert info.value.status == 500


def test_net_bad_json_detected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    with pytest.raises(FetchError) as info:
        get_json("https://example.com/x", transport=_transport(handler))
    assert info.value.kind == "bad_json"


def test_net_offline_detected():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(FetchError) as info:
        request_text("https://example.com/x", transport=_transport(handler))
    assert info.value.kind == "offline"


def test_net_timeout_detected():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(FetchError) as info:
        request_text("https://example.com/x", transport=_transport(handler))
    assert info.value.kind == "timeout"


def test_net_success_returns_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"].startswith("Mozilla/5.0")
        return httpx.Response(200, json={"ok": True})

    assert get_json("https://example.com/x", transport=_transport(handler)) == {"ok": True}
