# -*- coding: utf-8 -*-
"""插件市场测试：索引解析、离线缓存、sha256、zip slip 防护、安装 / 卸载。

全部离线：下载走 ``httpx.MockTransport``，官方源用临时目录里的包。

    python run_tests.py tests/test_market.py -q
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from endbox.config import Settings
from endbox.plugins import market as market_mod
from endbox.plugins.market import (
    InstallResult,
    MarketEntry,
    MarketError,
    MarketSource,
    Marketplace,
    default_market_dir,
    parse_entry,
    parse_index,
    safe_extract,
    sha256_file,
)
from endbox.version import __version__


# ---------------------------------------------------------------------------
# 构造测试数据
# ---------------------------------------------------------------------------


def _manifest(plugin_id: str = "demo", version: str = "0.1.0", **overrides) -> dict:
    data = {
        "id": plugin_id,
        "name": "演示插件",
        "version": version,
        "author": "tests",
        "description": "市场测试插件",
        "icon": "spark",
        "entry": "plugin.py",
        "min_app_version": "1.0.0",
        "permissions": [],
    }
    data.update(overrides)
    return data


def make_zip(
    tmp_path: Path,
    plugin_id: str = "demo",
    version: str = "0.1.0",
    *,
    top: str | None = None,
    manifest: dict | None = None,
    extra: dict[str, bytes] | None = None,
    name: str | None = None,
) -> tuple[Path, str]:
    """造一个合法的插件包 zip，返回 (路径, sha256)。"""
    package = Path(tmp_path) / (name or f"{plugin_id}-{version}.zip")
    folder = top if top is not None else plugin_id
    manifest = manifest if manifest is not None else _manifest(plugin_id, version)
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{folder}/plugin.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr(f"{folder}/plugin.py", "PLUGIN = True\n")
        zf.writestr(f"{folder}/README.md", "# demo\n")
        for relative, payload in (extra or {}).items():
            zf.writestr(f"{folder}/{relative}", payload)
    return package, sha256_file(package)


def entry_dict(
    plugin_id: str = "demo",
    version: str = "0.1.0",
    *,
    sha256: str = "0" * 64,
    download_url: str = "https://example.com/demo-0.1.0.zip",
    **overrides,
) -> dict:
    data = {
        "id": plugin_id,
        "name": "演示插件",
        "version": version,
        "author": "tests",
        "description": "市场测试插件",
        "icon": "spark",
        "category": "示例",
        "tags": ["测试", "演示"],
        "homepage": "https://example.com/demo",
        "download_url": download_url,
        "sha256": sha256,
        "size": 100,
        "min_app_version": "1.0.0",
        "permissions": [],
        "changelog": "第一条",
    }
    data.update(overrides)
    return data


def index_payload(entries: list[dict], *, schema: int = 1) -> dict:
    return {
        "schema": schema,
        "name": "测试市场",
        "updated": "2026-11-14",
        "plugins": entries,
    }


def source(**kwargs) -> MarketSource:
    data = {"name": "测试源", "url": "https://example.com/index.json", "official": False}
    data.update(kwargs)
    return MarketSource(**data)


def transport(body: bytes | str, *, status: int = 200, headers: dict | None = None):
    payload = body.encode("utf-8") if isinstance(body, str) else body

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=payload, headers=headers or {})

    return httpx.MockTransport(handler)


def failing_transport(message: str = "offline"):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(message)

    return httpx.MockTransport(handler)


def _make_marketplace(tmp_path: Path, **kwargs) -> Marketplace:
    return Marketplace(
        target_dir=tmp_path / "user_plugins",
        market_dir=tmp_path / "market",
        state_path=tmp_path / "market_state.json",
        cache_dir=tmp_path / "cache",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 索引解析
# ---------------------------------------------------------------------------


def test_parse_index_valid():
    index = parse_index(
        index_payload([entry_dict(), entry_dict("other", "1.2.3")]), source=source()
    )
    assert index.name == "测试市场"
    assert [entry.id for entry in index.entries] == ["demo", "other"]
    entry = index.entries[0]
    assert entry.version == "0.1.0"
    assert entry.category == "示例"
    assert entry.tags == ["测试", "演示"]
    assert entry.permissions == []
    assert index.categories() == ["示例"]


def test_parse_index_filters_unknown_permissions_and_bad_entries():
    payload = index_payload(
        [
            entry_dict(permissions=["timeline:read", "root:everything", "account:read"]),
            entry_dict("bad", sha256="not-a-hash"),
            {"id": "../evil"},
            "not-a-dict",
        ]
    )
    index = parse_index(payload, source=source())
    assert [entry.id for entry in index.entries] == ["demo"]
    assert index.entries[0].permissions == ["timeline:read", "account:read"]


def test_parse_index_rejects_bad_schema_and_shape():
    with pytest.raises(MarketError):
        parse_index({"schema": 2, "plugins": []}, source=source())
    with pytest.raises(MarketError):
        parse_index({"plugins": []}, source=source())
    with pytest.raises(MarketError):
        parse_index({"schema": 1, "plugins": "nope"}, source=source())
    with pytest.raises(MarketError):
        parse_index([], source=source())


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": ""},
        {"id": "a" * 70},
        {"version": "latest"},
        {"sha256": ""},
        {"sha256": "xyz"},
        {"download_url": ""},
        {"download_url": "ftp://example.com/x.zip"},
        {"download_url": "javascript:alert(1)"},
        {"min_app_version": "very-new"},
    ],
)
def test_parse_entry_rejects_invalid_fields(overrides):
    with pytest.raises(MarketError):
        parse_entry(entry_dict(**overrides), source=source())


def test_official_source_allows_relative_download_path():
    entry = parse_entry(
        entry_dict(download_url="packages/demo-0.1.0.zip"), source=source(official=True)
    )
    assert entry.download_url == "packages/demo-0.1.0.zip"


def test_entry_compatibility_and_update():
    entry = parse_entry(entry_dict(version="1.2.0", min_app_version="1.1.0"), source=source())
    assert entry.compatible(__version__)
    assert not entry.is_update_of("1.2.0")
    assert entry.is_update_of("1.1.0")
    assert not entry.is_update_of("2.0.0")
    incompatible = parse_entry(
        entry_dict(version="2.0.0", min_app_version="99.0.0"), source=source()
    )
    assert not incompatible.compatible()


# ---------------------------------------------------------------------------
# sha256 / 安全解压
# ---------------------------------------------------------------------------


def test_sha256_file_matches_hashlib(tmp_path):
    path = tmp_path / "x.bin"
    path.write_bytes(b"endbox-market")
    assert sha256_file(path) == hashlib.sha256(b"endbox-market").hexdigest()


def test_safe_extract_ok(tmp_path):
    package, _sha = make_zip(tmp_path)
    target = safe_extract(package, tmp_path / "extract", plugin_id="demo")
    assert (target / "plugin.json").is_file()
    assert (target / "README.md").is_file()
    assert target.name == "demo"


def test_safe_extract_rejects_zip_slip(tmp_path):
    package = tmp_path / "evil.zip"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("demo/plugin.json", json.dumps(_manifest()))
        zf.writestr("demo/plugin.py", "x=1\n")
        zf.writestr("../evil.py", "pwned")
    with pytest.raises(MarketError) as info:
        safe_extract(package, tmp_path / "extract", plugin_id="demo")
    assert "不安全" in str(info.value)
    assert not (tmp_path / "evil.py").exists()


def test_safe_extract_rejects_absolute_path(tmp_path):
    package = tmp_path / "abs.zip"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("demo/plugin.json", json.dumps(_manifest()))
        zf.writestr("demo/plugin.py", "x=1\n")
        zf.writestr("/tmp/evil.py", "pwned")
    with pytest.raises(MarketError):
        safe_extract(package, tmp_path / "extract", plugin_id="demo")


def test_safe_extract_rejects_symlink(tmp_path):
    package = tmp_path / "link.zip"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("demo/plugin.json", json.dumps(_manifest()))
        zf.writestr("demo/plugin.py", "x=1\n")
        info = zipfile.ZipInfo("demo/link.py")
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "target")
    with pytest.raises(MarketError) as exc:
        safe_extract(package, tmp_path / "extract", plugin_id="demo")
    assert "符号链接" in str(exc.value)


def test_safe_extract_requires_single_top_dir(tmp_path):
    flat = tmp_path / "flat.zip"
    with zipfile.ZipFile(flat, "w") as zf:
        zf.writestr("plugin.json", json.dumps(_manifest()))
        zf.writestr("plugin.py", "x=1\n")
    with pytest.raises(MarketError):
        safe_extract(flat, tmp_path / "extract", plugin_id="demo")

    multi = tmp_path / "multi.zip"
    with zipfile.ZipFile(multi, "w") as zf:
        zf.writestr("demo/plugin.json", json.dumps(_manifest()))
        zf.writestr("other/plugin.json", json.dumps(_manifest("other")))
    with pytest.raises(MarketError):
        safe_extract(multi, tmp_path / "extract", plugin_id="demo")


def test_safe_extract_requires_manifest(tmp_path):
    package = tmp_path / "nomani.zip"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("demo/plugin.py", "x=1\n")
    with pytest.raises(MarketError) as exc:
        safe_extract(package, tmp_path / "extract", plugin_id="demo")
    assert "plugin.json" in str(exc.value)


def test_safe_extract_rejects_id_mismatch(tmp_path):
    package, _ = make_zip(tmp_path, plugin_id="other")
    with pytest.raises(MarketError) as exc:
        safe_extract(package, tmp_path / "extract", plugin_id="demo")
    assert "不一致" in str(exc.value)


def test_safe_extract_limits_files_and_size(tmp_path, monkeypatch):
    package, _ = make_zip(tmp_path, extra={f"f{i}.txt": "x" for i in range(5)})
    monkeypatch.setattr(market_mod, "MAX_ARCHIVE_FILES", 2)
    with pytest.raises(MarketError):
        safe_extract(package, tmp_path / "extract", plugin_id="demo")

    package2, _ = make_zip(tmp_path, name="big.zip", extra={"big.txt": "x" * 100})
    monkeypatch.setattr(market_mod, "MAX_UNCOMPRESSED_BYTES", 10)
    with pytest.raises(MarketError):
        safe_extract(package2, tmp_path / "extract2", plugin_id="demo")


# ---------------------------------------------------------------------------
# 下载与安装（MockTransport，不联网）
# ---------------------------------------------------------------------------


def _entry_for_package(package: Path, **overrides) -> MarketEntry:
    sha = sha256_file(package)
    raw = entry_dict(
        sha256=sha,
        size=package.stat().st_size,
        download_url="https://example.com/demo-0.1.0.zip",
    )
    raw.update(overrides)
    return parse_entry(raw, source=source())


def test_install_from_http_with_progress(tmp_path):
    package, _ = make_zip(tmp_path)
    body = package.read_bytes()
    entry = _entry_for_package(package)
    market = _make_marketplace(tmp_path, transport=transport(body))
    progress: list[tuple[int, int]] = []
    result = market.install(entry, progress=lambda done, total: progress.append((done, total)))
    assert result.ok, result.error
    assert result.plugin_id == "demo"
    assert result.version == "0.1.0"
    assert (tmp_path / "user_plugins" / "demo" / "plugin.json").is_file()
    assert market.installed_version("demo") == "0.1.0"
    assert market.origin_of("demo") == "market"
    assert progress and progress[-1][0] == len(body)

    # 新实例读得到来源记录
    again = _make_marketplace(tmp_path)
    assert again.installed_record("demo") is not None
    assert again.installed_record("demo").sha256 == sha256_file(package)


def test_install_rejects_sha256_mismatch(tmp_path):
    package, _ = make_zip(tmp_path)
    entry = _entry_for_package(package, sha256="f" * 64)
    market = _make_marketplace(tmp_path, transport=transport(package.read_bytes()))
    result = market.install(entry)
    assert not result.ok
    assert "sha256" in (result.error or "").lower() or "校验" in (result.error or "")
    assert not (tmp_path / "user_plugins" / "demo").exists()


def test_install_rejects_version_mismatch(tmp_path):
    package, _ = make_zip(tmp_path, version="0.2.0")
    entry = _entry_for_package(package)
    market = _make_marketplace(tmp_path, transport=transport(package.read_bytes()))
    result = market.install(entry)
    assert not result.ok
    assert "版本" in (result.error or "")


def test_install_rejects_incompatible_min_version(tmp_path):
    package, _ = make_zip(tmp_path)
    entry = _entry_for_package(package, min_app_version="99.0.0")
    market = _make_marketplace(tmp_path, transport=transport(package.read_bytes()))
    result = market.install(entry)
    assert not result.ok
    assert "99.0.0" in (result.error or "")


def test_install_rejects_too_large_package(tmp_path, monkeypatch):
    package, _ = make_zip(tmp_path)
    entry = _entry_for_package(package)
    monkeypatch.setattr(market_mod, "MAX_PACKAGE_BYTES", 10)
    market = _make_marketplace(tmp_path, transport=transport(package.read_bytes()))
    result = market.install(entry)
    assert not result.ok
    assert "上限" in (result.error or "")


def test_install_from_official_relative_path_offline(tmp_path):
    market_dir = tmp_path / "market"
    package, _ = make_zip(tmp_path)
    target_pkg = market_dir / "packages" / "demo-0.1.0.zip"
    target_pkg.parent.mkdir(parents=True)
    target_pkg.write_bytes(package.read_bytes())
    raw = entry_dict(
        sha256=sha256_file(target_pkg),
        size=target_pkg.stat().st_size,
        download_url="packages/demo-0.1.0.zip",
    )
    entry = parse_entry(raw, source=source(official=True))
    market = Marketplace(
        target_dir=tmp_path / "user_plugins",
        market_dir=market_dir,
        state_path=tmp_path / "state.json",
        cache_dir=tmp_path / "cache",
    )
    result = market.install(entry)
    assert result.ok, result.error
    assert (tmp_path / "user_plugins" / "demo" / "plugin.py").is_file()


def test_install_overwrites_existing_plugin(tmp_path):
    package, _ = make_zip(tmp_path, version="0.2.0")
    target = tmp_path / "user_plugins" / "demo"
    target.mkdir(parents=True)
    (target / "old.txt").write_text("old", "utf-8")
    entry = _entry_for_package(package, version="0.2.0")
    market = _make_marketplace(tmp_path, transport=transport(package.read_bytes()))
    result = market.install(entry)
    assert result.ok, result.error
    assert result.replaced
    assert not (target / "old.txt").exists()
    assert market.installed_version("demo") == "0.2.0"


def test_uninstall_removes_directory_and_state(tmp_path):
    package, _ = make_zip(tmp_path)
    entry = _entry_for_package(package)
    market = _make_marketplace(tmp_path, transport=transport(package.read_bytes()))
    assert market.install(entry).ok
    assert market.uninstall("demo")
    assert not (tmp_path / "user_plugins" / "demo").exists()
    assert market.installed_record("demo") is None
    with pytest.raises(MarketError):
        market.uninstall("demo")


def test_uninstall_refuses_bundled_plugin(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    (bundled / "demo").mkdir(parents=True)
    (bundled / "demo" / "plugin.json").write_text(json.dumps(_manifest()), "utf-8")
    monkeypatch.setattr(market_mod.config, "PLUGINS_DIR", bundled)
    market = _make_marketplace(tmp_path)
    with pytest.raises(MarketError) as exc:
        market.uninstall("demo")
    assert "跟着程序" in str(exc.value) or "随程序分发" in str(exc.value)


# ---------------------------------------------------------------------------
# 索引缓存 / 离线降级
# ---------------------------------------------------------------------------


def test_fetch_index_writes_cache_and_reads_it(tmp_path):
    payload = index_payload([entry_dict()])
    market = _make_marketplace(tmp_path, transport=transport(json.dumps(payload)))
    src = source()
    first = market.fetch_index(src, force=True)
    assert not first.cached
    assert first.entries[0].id == "demo"

    # TTL 内不再请求网络
    second = market.fetch_index(src, force=False)
    assert second.cached
    assert second.entries[0].id == "demo"


def test_fetch_index_offline_uses_stale_cache(tmp_path):
    payload = index_payload([entry_dict()])
    good = _make_marketplace(tmp_path, transport=transport(json.dumps(payload)))
    assert good.fetch_index(source(), force=True).entries
    offline = _make_marketplace(tmp_path, transport=failing_transport())
    cached = offline.fetch_index(source(), force=True)
    assert cached.cached
    assert cached.entries[0].id == "demo"


def test_fetch_index_offline_without_cache_raises(tmp_path):
    market = _make_marketplace(tmp_path, transport=failing_transport())
    with pytest.raises(MarketError):
        market.fetch_index(source(), force=True)


def test_fetch_index_rejects_oversized_body(tmp_path, monkeypatch):
    payload = json.dumps(index_payload([entry_dict()]))
    market = _make_marketplace(tmp_path, transport=transport(payload))
    monkeypatch.setattr(market_mod, "MAX_INDEX_BYTES", 10)
    with pytest.raises(MarketError):
        market.fetch_index(source(), force=True)


# ---------------------------------------------------------------------------
# 来源管理 / 官方索引
# ---------------------------------------------------------------------------


def test_sources_add_remove_persist(tmp_path):
    settings = Settings(tmp_path / "settings.json")
    market = _make_marketplace(tmp_path, settings=settings)
    assert [item.official for item in market.sources()] == [True]

    added = market.add_source("https://example.com/index.json", "示例源")
    assert added.url == "https://example.com/index.json"
    assert len(market.sources()) == 2
    with pytest.raises(MarketError):
        market.add_source("https://example.com/index.json")
    with pytest.raises(MarketError):
        market.add_source("ftp://example.com/index.json")

    again = _make_marketplace(tmp_path, settings=Settings(tmp_path / "settings.json"))
    assert [item.url for item in again.sources() if not item.official] == [
        "https://example.com/index.json"
    ]

    assert again.remove_source("https://example.com/index.json")
    assert len(again.sources()) == 1
    assert not again.remove_source("https://example.com/index.json")


def test_load_catalog_official_plus_third_party(tmp_path):
    market_dir = tmp_path / "market"
    market_dir.mkdir(parents=True)
    official = index_payload([entry_dict(download_url="packages/demo-0.1.0.zip")])
    (market_dir / "index.json").write_text(json.dumps(official), "utf-8")
    settings = Settings(tmp_path / "settings.json")
    market = _make_marketplace(tmp_path, settings=settings)
    market.add_source("https://example.com/index.json", "示例源")
    third = index_payload([entry_dict("third", download_url="https://example.com/t.zip")])
    market._transport = transport(json.dumps(third))  # noqa: SLF001 - 测试注入
    catalog = market.load_catalog(force=True)
    assert [entry.id for entry in catalog.entries] == ["demo", "third"]
    assert catalog.errors == []


def test_load_catalog_reports_missing_official_index(tmp_path):
    market = _make_marketplace(tmp_path)
    catalog = market.load_catalog(force=True)
    assert catalog.indexes == []
    assert catalog.errors


def test_repo_official_index_matches_packages():
    """仓库里的官方索引必须和 market/packages 里的包一致（防止忘记重新打包）。"""
    market = Marketplace(
        target_dir=Path(default_market_dir()).parent / "data" / "plugins",
        market_dir=default_market_dir(),
    )
    index = market.official_index()
    assert len(index.entries) >= 5
    ids = {entry.id for entry in index.entries}
    assert {"timers", "timeline_stats", "permission_demo", "operator_roster", "link_notes"} <= ids
    for entry in index.entries:
        package = default_market_dir() / entry.download_url
        assert package.is_file(), entry.download_url
        assert sha256_file(package) == entry.sha256
        assert package.stat().st_size == entry.size
